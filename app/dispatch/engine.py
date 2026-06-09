"""Dispatch engine: consume ``incident.reported`` → match → assign → emit.

The engine is channel-agnostic: it consumes the normalised ``IncidentReported``
event and never asks which channel it came from (except to label the latency
metric). The matching/persistence core is factored into ``dispatch_incident`` so
it is unit-testable without RabbitMQ; the consumer is a thin wrapper that
deserialises a message, calls the core, and publishes ``incident.assigned``.

On every state transition we append an ``EventLog`` row. The no-responder path is
handled gracefully — logged, audited, and the incident stays ``REPORTED`` for a
later retry — never a crash.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import aio_pika
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from dispatch.geo import resolve_location
from dispatch.hospital_matcher import match_hospital
from dispatch.responder_matcher import RESPONDERS_GEO_KEY, match_nearest
from dispatch.triage import needs_hospital, severity_to_priority
from events.audit import record_event_log
from events.bus import EventBus
from events.schemas import (
    RK_ASSIGNED,
    RK_REPORTED,
    IncidentAssigned,
    IncidentReported,
)
from models.db import (
    Assignment,
    Incident,
    IncidentStatus,
    Responder,
    ResponderStatus,
    ResponderType,
    async_session,
)
from utils import metrics
from utils.logging import get_logger
from utils.redis import make_redis

log = get_logger("beacon.dispatch")

DISPATCH_QUEUE = "dispatch.incoming"
_MEDICAL_SPECIALTY = "TRAUMA"


@dataclass
class DispatchOutcome:
    assigned: bool
    assignment_id: str | None = None
    responder_id: str | None = None
    hospital_id: str | None = None
    match_radius_m: float | None = None
    assigned_event: IncidentAssigned | None = None


async def refresh_active_responders(db: AsyncSession) -> None:
    """Reset the active-responders gauge from the DB (available, by type)."""
    rows = await db.execute(
        select(Responder.type, func.count())
        .where(Responder.status == ResponderStatus.AVAILABLE)
        .group_by(Responder.type)
    )
    counts = {rtype: 0 for rtype in ResponderType}
    for rtype, count in rows.all():
        counts[rtype] = count
    for rtype, count in counts.items():
        metrics.active_responders.labels(type=rtype.value).set(count)


async def dispatch_incident(
    db: AsyncSession,
    redis,
    payload: IncidentReported,
    *,
    geo_key: str = RESPONDERS_GEO_KEY,
) -> DispatchOutcome:
    """Match + assign a reported incident. Pure of the bus, so it unit-tests."""
    settings = get_settings()
    lat, lng = resolve_location(payload.latitude, payload.longitude, payload.area_label)

    match = await match_nearest(
        db,
        redis,
        emergency_type=payload.emergency_type,
        latitude=lat,
        longitude=lng,
        start_radius_m=settings.responder_search_radius_m,
        max_radius_m=settings.responder_max_radius_m,
        geo_key=geo_key,
    )

    if match is None:
        await record_event_log(
            db,
            payload.incident_id,
            "dispatch.no_responder",
            {
                "emergency_type": payload.emergency_type.value,
                "severity": payload.severity.value,
            },
        )
        await db.commit()
        log.warning(
            "dispatch.no_responder",
            incident_id=payload.incident_id,
            emergency_type=payload.emergency_type.value,
        )
        return DispatchOutcome(assigned=False)

    responder = match.responder

    hospital = None
    if needs_hospital(payload.emergency_type):
        hospital_match = await match_hospital(
            db, latitude=lat, longitude=lng, required_specialty=_MEDICAL_SPECIALTY
        )
        hospital = hospital_match.hospital if hospital_match else None

    incident = await db.get(Incident, payload.incident_id)
    assignment = Assignment(
        incident_id=payload.incident_id,
        responder_id=responder.id,
        hospital_id=hospital.id if hospital else None,
    )
    db.add(assignment)
    responder.status = ResponderStatus.ASSIGNED
    if incident is not None:
        incident.status = IncidentStatus.ASSIGNED

    await record_event_log(
        db,
        payload.incident_id,
        RK_ASSIGNED,
        {
            "responder_id": responder.id,
            "hospital_id": hospital.id if hospital else None,
            "match_radius_m": round(match.distance_m, 1),
        },
    )
    await db.commit()

    # Metrics (post-commit; process-wide collectors).
    latency_s = (datetime.now(UTC) - payload.reported_at).total_seconds()
    metrics.dispatch_latency_seconds.labels(
        source_channel=payload.source_channel.value
    ).observe(max(0.0, latency_s))
    metrics.match_radius_meters.observe(match.distance_m)
    await refresh_active_responders(db)

    assigned_event = IncidentAssigned(
        incident_id=payload.incident_id,
        responder_id=responder.id,
        responder_name=responder.name,
        responder_contact=responder.contact,
        reachable_channel=responder.reachable_channel,
        hospital_id=hospital.id if hospital else None,
        hospital_name=hospital.name if hospital else None,
        source_channel=payload.source_channel,
        severity=payload.severity,
        match_radius_m=match.distance_m,
    )
    log.info(
        "dispatch.assigned",
        incident_id=payload.incident_id,
        responder_id=responder.id,
        hospital_id=hospital.id if hospital else None,
        radius_m=round(match.distance_m, 1),
    )
    return DispatchOutcome(
        assigned=True,
        assignment_id=assignment.id,
        responder_id=responder.id,
        hospital_id=hospital.id if hospital else None,
        match_radius_m=match.distance_m,
        assigned_event=assigned_event,
    )


def _make_handler(bus: EventBus):
    async def handler(message: aio_pika.abc.AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            payload = IncidentReported.model_validate_json(message.body)
            redis = make_redis()
            try:
                async with async_session() as db:
                    outcome = await dispatch_incident(db, redis, payload)
            finally:
                await redis.aclose()
            if outcome.assigned and outcome.assigned_event is not None:
                # The assignment is already committed; a broker hiccup here must
                # not reject the message and re-dispatch. Log and move on.
                try:
                    await bus.publish(
                        RK_ASSIGNED,
                        outcome.assigned_event,
                        priority=severity_to_priority(payload.severity),
                    )
                except Exception as exc:  # noqa: BLE001
                    log.error(
                        "dispatch.assigned_publish_failed",
                        incident_id=payload.incident_id,
                        error=str(exc),
                    )

    return handler


async def start_dispatch_consumer(bus: EventBus) -> aio_pika.abc.AbstractQueue:
    """Declare the priority dispatch queue and begin consuming (background)."""
    queue = await bus.declare_queue(DISPATCH_QUEUE, [RK_REPORTED])
    await queue.consume(_make_handler(bus))
    log.info("dispatch.consumer_started", queue=DISPATCH_QUEUE)
    return queue
