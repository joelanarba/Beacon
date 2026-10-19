"""Publication, retry, and duplicate-processing failure boundaries."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from channels.base import ChannelKind, EmergencyType, IncidentEvent, Severity
from events import outbox
from events.outbox import (
    RetryableDeliveryError,
    consume_reliably,
    record_failure,
    relay_one,
)
from incidents import acceptance
from incidents.acceptance import IdempotencyConflict, accept_incident
from models.db import DeadLetter, EventLog, EventOutbox, Incident


def report(description="Test emergency"):
    return IncidentEvent(
        source_channel=ChannelKind.APP,
        emergency_type=EmergencyType.FIRE,
        severity=Severity.CRITICAL,
        description=description,
        latitude=5.56,
        longitude=-0.2,
    )


class Message:
    def __init__(self, *, body=b'{"incident_id":"test"}', attempt=0):
        self.body = body
        self.routing_key = "incident.reported"
        self.message_id = "test-event"
        self.priority = 10
        self.headers = {"x-beacon-attempt": attempt}
        self.ack = AsyncMock()
        self.nack = AsyncMock()


async def test_acceptance_is_durable_without_a_broker(db_session):
    incident = await accept_incident(db_session, report(), "request-1")
    event = (await db_session.execute(select(EventOutbox))).scalar_one()
    assert event.payload["incident_id"] == incident.id
    assert event.published_at is None
    assert event.priority == 10
    assert (await db_session.scalar(select(func.count()).select_from(EventLog))) == 1


async def test_ingest_retry_returns_original_incident(db_session):
    first = await accept_incident(db_session, report(), "same-request")
    second = await accept_incident(db_session, report(), "same-request")
    assert first.id == second.id
    assert (await db_session.scalar(select(func.count()).select_from(Incident))) == 1
    assert (await db_session.scalar(select(func.count()).select_from(EventOutbox))) == 1


async def test_ingest_key_cannot_be_reused_for_other_content(db_session):
    await accept_incident(db_session, report(), "same-request")
    with pytest.raises(IdempotencyConflict):
        await accept_incident(db_session, report("Different emergency"), "same-request")
    assert (await db_session.scalar(select(func.count()).select_from(Incident))) == 1


async def test_identical_reports_without_identity_are_not_merged(db_session):
    first = await accept_incident(db_session, report())
    second = await accept_incident(db_session, report())
    assert first.id != second.id


async def test_concurrent_ingest_retry_creates_one_incident(session_factory):
    async def accept():
        async with session_factory() as db:
            return (await accept_incident(db, report(), "concurrent-request")).id

    ids = await asyncio.gather(*(accept() for _ in range(8)))
    assert len(set(ids)) == 1
    async with session_factory() as db:
        assert (await db.scalar(select(func.count()).select_from(Incident))) == 1
        assert (await db.scalar(select(func.count()).select_from(EventOutbox))) == 1


async def test_outbox_failure_rolls_back_incident_acceptance(
    session_factory, monkeypatch
):
    monkeypatch.setattr(
        acceptance, "enqueue_event", AsyncMock(side_effect=RuntimeError)
    )
    event = report()
    with pytest.raises(RuntimeError):
        async with session_factory() as db:
            await accept_incident(db, event, "failed-transaction")
    async with session_factory() as db:
        assert await db.get(Incident, event.incident_id) is None
        assert (await db.scalar(select(func.count()).select_from(EventOutbox))) == 0


async def test_relay_preserves_failure_and_retries_same_message_identity(db_session):
    await accept_incident(db_session, report(), "publish-retry")
    bus = SimpleNamespace(publish=AsyncMock(side_effect=TimeoutError))
    assert await relay_one(db_session, bus)
    event = (await db_session.execute(select(EventOutbox))).scalar_one()
    assert event.published_at is None
    assert event.publish_attempts == 1
    assert event.last_error == "TimeoutError"
    event.available_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    bus.publish.side_effect = None
    assert await relay_one(db_session, bus)
    assert event.published_at is not None
    assert event.last_error is None
    ids = [call.kwargs["message_id"] for call in bus.publish.await_args_list]
    assert ids == [event.event_id, event.event_id]


async def test_concurrent_relays_do_not_publish_same_pending_row(session_factory):
    async with session_factory() as db:
        await accept_incident(db, report(), "relay-lock")
    entered, release = asyncio.Event(), asyncio.Event()

    async def publish(*args, **kwargs):
        entered.set()
        await release.wait()

    bus = SimpleNamespace(publish=AsyncMock(side_effect=publish))

    async def relay():
        async with session_factory() as db:
            return await relay_one(db, bus)

    first = asyncio.create_task(relay())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert not await relay()
    finally:
        release.set()
        await first
    assert bus.publish.await_count == 1


async def test_transient_failure_commits_one_delayed_retry(db_session):
    message = Message()
    for _ in range(2):
        await record_failure(db_session, message, "dispatch", RuntimeError(), 0)
    event = (await db_session.execute(select(EventOutbox))).scalar_one()
    assert event.event_id == message.message_id
    assert event.delivery_attempt == 1
    assert event.available_at > datetime.now(UTC)
    assert (await db_session.scalar(select(func.count()).select_from(DeadLetter))) == 0


async def test_exhausted_failure_is_retained_once(db_session):
    message = Message(attempt=outbox.get_settings().delivery_max_retries)
    for _ in range(2):
        await record_failure(
            db_session,
            message,
            "dispatch",
            RuntimeError(),
            outbox.get_settings().delivery_max_retries,
        )
    dead = (await db_session.execute(select(DeadLetter))).scalar_one()
    assert dead.body == message.body
    assert dead.error_type == "RuntimeError"
    assert (await db_session.scalar(select(func.count()).select_from(EventOutbox))) == 0


async def test_malformed_message_is_quarantined_without_retry(db_session):
    message = Message(body=b"not-json")
    await record_failure(db_session, message, "dispatch", ValueError(), 0)
    dead = (await db_session.execute(select(DeadLetter))).scalar_one()
    assert dead.body == b"not-json"
    assert (await db_session.scalar(select(func.count()).select_from(EventOutbox))) == 0


async def test_consumer_success_acknowledges_without_failure_storage(monkeypatch):
    store = AsyncMock()
    monkeypatch.setattr(outbox, "record_failure", store)
    message = Message()
    await consume_reliably(message, "dispatch", AsyncMock())
    message.ack.assert_awaited_once()
    store.assert_not_awaited()


async def test_consumer_acknowledges_only_after_durable_handoff(monkeypatch):
    message = Message()

    @asynccontextmanager
    async def session():
        yield object()

    async def store(*args):
        message.ack.assert_not_awaited()

    monkeypatch.setattr(outbox, "async_session", session)
    monkeypatch.setattr(outbox, "record_failure", AsyncMock(side_effect=store))
    await consume_reliably(
        message,
        "dispatch",
        AsyncMock(side_effect=RetryableDeliveryError),
    )
    message.ack.assert_awaited_once()
    message.nack.assert_not_awaited()


async def test_failed_handoff_requeues_without_acknowledging(monkeypatch):
    message = Message()

    @asynccontextmanager
    async def unavailable():
        raise ConnectionError()
        yield

    monkeypatch.setattr(outbox, "async_session", unavailable)
    monkeypatch.setattr(outbox.asyncio, "sleep", AsyncMock())
    await consume_reliably(message, "dispatch", AsyncMock(side_effect=RuntimeError))
    message.nack.assert_awaited_once_with(requeue=True)
    message.ack.assert_not_awaited()


async def test_worker_retries_failed_initial_broker_setup(monkeypatch):
    from dispatch import engine
    from events import worker
    from notification import notifier

    stop = asyncio.Event()
    attempts = []

    class Bus:
        def __init__(self, *args):
            attempts.append(self)
            self.close = AsyncMock()

        async def connect(self):
            if len(attempts) == 1:
                raise ConnectionError()

    async def ready(*args):
        stop.set()

    monkeypatch.setattr(worker, "EventBus", Bus)
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: SimpleNamespace(
            amqp_url="unused",
            event_exchange="unused",
            max_queue_priority=10,
            broker_timeout_seconds=1,
            delivery_retry_base_seconds=0.001,
        ),
    )
    monkeypatch.setattr(engine, "start_dispatch_consumer", AsyncMock())
    monkeypatch.setattr(
        notifier, "start_notification_consumer", AsyncMock(side_effect=ready)
    )
    app = SimpleNamespace(state=SimpleNamespace(bus=None))
    await asyncio.wait_for(worker.run_event_worker(app, object(), stop), 5)
    assert len(attempts) == 2
    assert all(bus.close.await_count == 1 for bus in attempts)
    assert app.state.bus is None
