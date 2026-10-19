"""Transactional publication and durable consumer failure handoff.

A crash after broker confirmation but before the outbox commit can republish an
event. Consumers must therefore tolerate redelivery; publication is not exactly-once.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from models.db import DeadLetter, EventOutbox, async_session
from utils.logging import get_logger

log = get_logger("beacon.outbox")


class RetryableDeliveryError(Exception):
    """Processing cannot complete yet, for example while resources are busy."""


def retry_delay(attempt: int) -> float:
    settings = get_settings()
    return min(
        settings.delivery_retry_base_seconds * 2 ** min(max(attempt - 1, 0), 20),
        settings.delivery_retry_max_seconds,
    )


async def enqueue_event(
    session: AsyncSession,
    routing_key: str,
    payload: BaseModel | dict,
    *,
    event_id: str,
    priority: int = 0,
    delivery_attempt: int = 0,
    available_at: datetime | None = None,
) -> None:
    """Record intent without committing; a logical delivery attempt is unique."""
    await session.execute(
        insert(EventOutbox)
        .values(
            event_id=event_id,
            routing_key=routing_key,
            payload=(
                payload.model_dump(mode="json")
                if isinstance(payload, BaseModel)
                else payload
            ),
            priority=priority,
            delivery_attempt=delivery_attempt,
            available_at=available_at or datetime.now(UTC),
            publish_attempts=0,
        )
        .on_conflict_do_nothing(constraint="uq_outbox_delivery")
    )


async def relay_one(session: AsyncSession, bus) -> bool:
    """Publish one due event under a row lock; leave failures durably pending."""
    event = (
        await session.execute(
            select(EventOutbox)
            .where(
                EventOutbox.published_at.is_(None),
                EventOutbox.available_at <= datetime.now(UTC),
            )
            .order_by(
                EventOutbox.priority.desc(), EventOutbox.created_at, EventOutbox.id
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if event is None:
        return False

    event.publish_attempts += 1
    try:
        await bus.publish(
            event.routing_key,
            event.payload,
            priority=event.priority,
            message_id=event.event_id,
            headers={"x-beacon-attempt": event.delivery_attempt},
        )
    except Exception as exc:
        event.last_error = type(exc).__name__[:128]
        event.available_at = datetime.now(UTC) + timedelta(
            seconds=retry_delay(event.publish_attempts)
        )
        log.warning("outbox.publish_pending", error_type=event.last_error)
    else:
        event.published_at = datetime.now(UTC)
        event.last_error = None
    await session.commit()
    return True


def message_identity(message) -> str:
    if isinstance(message.message_id, str) and 0 < len(message.message_id) <= 200:
        return message.message_id
    digest = hashlib.sha256(message.routing_key.encode() + b"\0" + message.body)
    return "legacy:" + digest.hexdigest()


async def record_failure(
    session: AsyncSession, message, consumer: str, error: Exception, attempt: int
) -> None:
    """Commit a retry or dead letter before the original delivery is acknowledged."""
    settings = get_settings()
    event_id = message_identity(message)
    permanent = isinstance(error, (ValidationError, ValueError, UnicodeError))
    if not permanent and attempt < settings.delivery_max_retries:
        # Validation already succeeded in the consumer for transient failures.
        await enqueue_event(
            session,
            message.routing_key,
            json.loads(message.body),
            event_id=event_id,
            priority=message.priority or 0,
            delivery_attempt=attempt + 1,
            available_at=datetime.now(UTC)
            + timedelta(seconds=retry_delay(attempt + 1)),
        )
    else:
        await session.execute(
            insert(DeadLetter)
            .values(
                consumer=consumer,
                event_id=event_id,
                routing_key=message.routing_key,
                body=message.body,
                delivery_attempt=attempt,
                error_type=type(error).__name__[:128],
            )
            .on_conflict_do_nothing(constraint="uq_dead_letter_event")
        )
    await session.commit()


async def consume_reliably(message, consumer: str, process) -> None:
    """Acknowledge success or a durable failure handoff, never an unrecorded error."""
    attempt = (message.headers or {}).get("x-beacon-attempt", 0)
    try:
        if (
            not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or not 0 <= attempt <= get_settings().delivery_max_retries
        ):
            attempt = 0
            raise ValueError("Invalid delivery attempt header")
        await process()
    except Exception as exc:
        try:
            async with async_session() as session:
                await record_failure(session, message, consumer, exc, attempt)
        except Exception as handoff_error:
            log.error(
                "delivery.handoff_failed", error_type=type(handoff_error).__name__
            )
            # PostgreSQL may be down too. Retain the broker delivery and avoid a
            # tight redelivery loop until durable retry storage is available.
            await asyncio.sleep(get_settings().delivery_retry_base_seconds)
            await message.nack(requeue=True)
            return
    await message.ack()
