"""Dispatch engine (DB + Redis): nearest available responder, hospital capacity
respected, and the no-responder path stays graceful. No RabbitMQ here —
``dispatch_incident`` is factored to be bus-free so the matching logic unit-tests
directly. (The priority/bus behaviour is proven in test_bus_priority.py.)
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from channels.base import ChannelKind, EmergencyType, Severity
from dispatch.engine import dispatch_incident
from dispatch.hospital_matcher import match_hospital
from dispatch.responder_matcher import match_nearest
from events.schemas import IncidentReported
from models.db import (
    Assignment,
    EventLog,
    Hospital,
    Incident,
    IncidentStatus,
    ReachableChannel,
    Responder,
    ResponderStatus,
    ResponderType,
)
from utils.redis import make_redis

OSU = (5.5558, -0.1769)  # (lat, lng)


async def _responder(db, name, rtype, status):
    responder = Responder(
        name=name,
        type=rtype,
        status=status,
        contact="+233200000000",
        reachable_channel=ReachableChannel.SMS,
    )
    db.add(responder)
    await db.flush()
    return responder


async def _incident(db, *, etype, severity, latitude=None, longitude=None, area=None):
    incident = Incident(
        reporter_contact="+233200000000",
        emergency_type=etype,
        severity=severity,
        status=IncidentStatus.REPORTED,
        source_channel=ChannelKind.USSD,
        description="test",
        latitude=latitude,
        longitude=longitude,
        area_label=area,
    )
    db.add(incident)
    await db.flush()
    return incident


# --------------------------------------------------------------------------- #
# Responder matching
# --------------------------------------------------------------------------- #
async def test_match_nearest_skips_busy_and_picks_nearest_available(db_session):
    rc = make_redis()
    geo_key = f"test:geo:{uuid.uuid4().hex}"
    lat, lng = OSU

    near_busy = await _responder(
        db_session, "Amb-near-busy", ResponderType.AMBULANCE, ResponderStatus.ASSIGNED
    )
    mid_free = await _responder(
        db_session, "Amb-mid-free", ResponderType.AMBULANCE, ResponderStatus.AVAILABLE
    )
    far_free = await _responder(
        db_session, "Amb-far-free", ResponderType.AMBULANCE, ResponderStatus.AVAILABLE
    )
    await rc.geoadd(geo_key, [lng, lat, near_busy.id])  # closest, but ASSIGNED
    await rc.geoadd(geo_key, [lng + 0.01, lat + 0.01, mid_free.id])
    await rc.geoadd(geo_key, [lng + 0.05, lat + 0.05, far_free.id])

    match = await match_nearest(
        db_session,
        rc,
        emergency_type=EmergencyType.MEDICAL,
        latitude=lat,
        longitude=lng,
        start_radius_m=5000,
        max_radius_m=25000,
        geo_key=geo_key,
    )
    await rc.delete(geo_key)
    await rc.aclose()

    assert match is not None
    assert match.responder.id == mid_free.id  # nearest AVAILABLE, busy one skipped


# --------------------------------------------------------------------------- #
# Hospital capacity
# --------------------------------------------------------------------------- #
async def test_hospital_capacity_respected(db_session):
    specialty = f"SPEC_{uuid.uuid4().hex[:8]}"  # unique → isolates from seed data
    hospital = Hospital(
        name="Test Hospital",
        latitude=OSU[0],
        longitude=OSU[1],
        total_capacity=1,
        available_capacity=1,
        specialties=[specialty],
    )
    db_session.add(hospital)
    await db_session.flush()

    first = await match_hospital(
        db_session, latitude=OSU[0], longitude=OSU[1], required_specialty=specialty
    )
    assert first is not None and first.hospital.id == hospital.id

    # The bed is now reserved (available_capacity -> 0): no further match.
    second = await match_hospital(
        db_session, latitude=OSU[0], longitude=OSU[1], required_specialty=specialty
    )
    assert second is None


# --------------------------------------------------------------------------- #
# End-to-end dispatch (bus-free core)
# --------------------------------------------------------------------------- #
async def test_dispatch_incident_assigns_and_audits(db_session):
    rc = make_redis()
    geo_key = f"test:geo:{uuid.uuid4().hex}"
    incident = await _incident(
        db_session, etype=EmergencyType.FIRE, severity=Severity.CRITICAL, area="Osu"
    )
    responder = await _responder(
        db_session, "Fire-1", ResponderType.FIRE, ResponderStatus.AVAILABLE
    )
    await rc.geoadd(geo_key, [OSU[1], OSU[0], responder.id])

    payload = IncidentReported(
        incident_id=incident.id,
        source_channel=ChannelKind.USSD,
        emergency_type=EmergencyType.FIRE,
        severity=Severity.CRITICAL,
        area_label="Osu",
    )
    outcome = await dispatch_incident(db_session, rc, payload, geo_key=geo_key)
    await rc.delete(geo_key)
    await rc.aclose()

    assert outcome.assigned
    assert outcome.responder_id == responder.id
    assert (
        await db_session.get(Incident, incident.id)
    ).status is IncidentStatus.ASSIGNED
    assert (
        await db_session.get(Responder, responder.id)
    ).status is ResponderStatus.ASSIGNED

    assignment = (
        (
            await db_session.execute(
                select(Assignment).where(Assignment.incident_id == incident.id)
            )
        )
        .scalars()
        .first()
    )
    assert assignment is not None and assignment.responder_id == responder.id

    logs = (
        (
            await db_session.execute(
                select(EventLog).where(EventLog.incident_id == incident.id)
            )
        )
        .scalars()
        .all()
    )
    assert any(e.event_type == "incident.assigned" for e in logs)


async def test_dispatch_incident_no_responder_stays_reported(db_session):
    rc = make_redis()
    empty_key = f"test:geo:empty:{uuid.uuid4().hex}"  # never populated
    incident = await _incident(
        db_session,
        etype=EmergencyType.FIRE,
        severity=Severity.CRITICAL,
        latitude=OSU[0],
        longitude=OSU[1],
    )
    payload = IncidentReported(
        incident_id=incident.id,
        source_channel=ChannelKind.SMS,
        emergency_type=EmergencyType.FIRE,
        severity=Severity.CRITICAL,
        latitude=OSU[0],
        longitude=OSU[1],
    )
    outcome = await dispatch_incident(db_session, rc, payload, geo_key=empty_key)
    await rc.aclose()

    assert outcome.assigned is False
    assert (
        await db_session.get(Incident, incident.id)
    ).status is IncidentStatus.REPORTED

    logs = (
        (
            await db_session.execute(
                select(EventLog).where(EventLog.incident_id == incident.id)
            )
        )
        .scalars()
        .all()
    )
    assert any(e.event_type == "dispatch.no_responder" for e in logs)
