"""Dispatch engine: consume ``incident.reported`` -> match -> assign -> emit.

The engine is channel-agnostic: it consumes the normalised ``IncidentReported``
event and never asks which channel it came from (except to label the latency
metric). The matching/persistence core is factored into ``dispatch_incident`` so
it can be tested without RabbitMQ. Assignment changes and their outbound event
commit together. Incident row locking and assignment constraints make redelivery
idempotent. Resource shortages use bounded durable retry, then dead-letter storage.
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
from events.outbox import RetryableDeliveryError, consume_reliably, enqueue_event
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
    retryable: bool = False
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
    incident = (
        await db.execute(
            select(Incident)
            .where(Incident.id == payload.incident_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if incident is None:
        raise ValueError("Unknown incident")
    if incident.status != IncidentStatus.REPORTED:
        await db.commit()
        return DispatchOutcome(assigned=False)

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
        return DispatchOutcome(assigned=False, retryable=True)

    responder = match.responder

    hospital = None
    if needs_hospital(payload.emergency_type):
        hospital_match = await match_hospital(
            db, latitude=lat, longitude=lng, required_specialty=_MEDICAL_SPECIALTY
        )
        hospital = hospital_match.hospital if hospital_match else None
        if hospital is None:
            await record_event_log(db, payload.incident_id, "dispatch.no_hospital")
            await db.commit()
            return DispatchOutcome(assigned=False, retryable=True)

    assignment = Assignment(
        incident_id=payload.incident_id,
        responder_id=responder.id,
        hospital_id=hospital.id if hospital else None,
    )
    db.add(assignment)
    responder.status = ResponderStatus.ASSIGNED
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
    await enqueue_event(
        db,
        RK_ASSIGNED,
        assigned_event,
        event_id=f"{RK_ASSIGNED}:{payload.incident_id}",
        priority=severity_to_priority(payload.severity),
    )
    await db.commit()

    latency_s = (datetime.now(UTC) - payload.reported_at).total_seconds()
    metrics.dispatch_latency_seconds.labels(
        source_channel=payload.source_channel.value
    ).observe(max(0.0, latency_s))
    metrics.match_radius_meters.observe(match.distance_m)
    try:
        await refresh_active_responders(db)
    except Exception as exc:
        log.warning("dispatch.gauge_refresh_failed", error_type=type(exc).__name__)
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
        async def process() -> None:
            payload = IncidentReported.model_validate_json(message.body)
            redis = make_redis()
            try:
                async with async_session() as db:
                    outcome = await dispatch_incident(db, redis, payload)
            finally:
                await redis.aclose()
            if outcome.retryable:
                raise RetryableDeliveryError("Dispatch resources unavailable")

        await consume_reliably(message, "dispatch", process)

    return handler


async def start_dispatch_consumer(bus: EventBus) -> aio_pika.abc.AbstractQueue:
    """Declare the priority dispatch queue and begin consuming (background)."""
    queue = await bus.declare_queue(DISPATCH_QUEUE, [RK_REPORTED])
    await queue.consume(_make_handler(bus))
    log.info("dispatch.consumer_started", queue=DISPATCH_QUEUE)
    return queue
