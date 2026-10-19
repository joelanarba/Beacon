"""Persist a report and its dispatch event in one transaction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from channels.base import IncidentEvent
from events.audit import record_event_log
from events.outbox import enqueue_event
from events.schemas import RK_REPORTED, IncidentReported
from models.db import Incident, IncidentStatus
from utils import metrics


class IdempotencyConflict(ValueError):
    """A previously accepted key was reused for different report content."""


def ingest_key(event: IncidentEvent, identity: str) -> str:
    return hashlib.sha256(
        f"{event.source_channel.value}\0{identity}".encode()
    ).hexdigest()


def request_hash(event: IncidentEvent) -> str:
    fields = asdict(event)
    fields.pop("incident_id")
    return hashlib.sha256(
        json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def find_accepted(
    session: AsyncSession, event: IncidentEvent, identity: str
) -> Incident | None:
    incident = (
        await session.execute(
            select(Incident).where(Incident.ingest_key == ingest_key(event, identity))
        )
    ).scalar_one_or_none()
    if incident is not None and incident.request_hash != request_hash(event):
        raise IdempotencyConflict("Idempotency key was used for a different report")
    return incident


async def accept_incident(
    session: AsyncSession, event: IncidentEvent, identity: str | None = None
) -> Incident:
    fields = asdict(event)
    fields["id"] = fields.pop("incident_id")
    fields["status"] = IncidentStatus.REPORTED
    if identity is not None:
        fields.update(
            ingest_key=ingest_key(event, identity), request_hash=request_hash(event)
        )
        incident = (
            await session.execute(
                insert(Incident)
                .values(**fields)
                .on_conflict_do_nothing(index_elements=[Incident.ingest_key])
                .returning(Incident)
            )
        ).scalar_one_or_none()
        if incident is None:
            incident = await find_accepted(session, event, identity)
            if incident is None:
                raise RuntimeError("Accepted report could not be read")
            await session.commit()
            return incident
    else:
        incident = Incident(**fields)
        session.add(incident)
        await session.flush()

    reported = IncidentReported.from_event(event)
    reported.reported_at = incident.created_at or datetime.now(UTC)
    await record_event_log(session, incident.id, RK_REPORTED)
    await enqueue_event(
        session,
        RK_REPORTED,
        reported,
        event_id=f"{RK_REPORTED}:{incident.id}",
        priority=event.severity.numeric_priority(),
    )
    await session.commit()
    metrics.incidents_total.labels(
        source_channel=event.source_channel.value,
        severity=event.severity.value,
        status=IncidentStatus.REPORTED.value,
    ).inc()
    return incident
