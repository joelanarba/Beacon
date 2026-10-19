"""State and concurrency invariants against a freshly migrated PostgreSQL schema."""

from __future__ import annotations

import asyncio
import uuid
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from channels.base import ChannelKind, EmergencyType, Severity
from dispatch.engine import dispatch_incident
from dispatch.hospital_matcher import match_hospital
from events.schemas import IncidentReported
from incidents.api import advance_status
from models.db import (
    Assignment,
    EventLog,
    EventOutbox,
    Hospital,
    Incident,
    IncidentStatus,
    ReachableChannel,
    Responder,
    ResponderStatus,
    ResponderType,
)
from models.schemas import StatusUpdate


async def scenario(db, *, capacity=1, assigned=True, hospital=None, responder=None):
    if hospital is None:
        hospital = Hospital(
            name="Test hospital",
            latitude=5.56,
            longitude=-0.2,
            total_capacity=capacity,
            available_capacity=0 if assigned else capacity,
            specialties=["TRAUMA"],
        )
        db.add(hospital)
    if responder is None:
        responder = Responder(
            name="Test responder",
            type=ResponderType.FIRE,
            status=ResponderStatus.ASSIGNED if assigned else ResponderStatus.AVAILABLE,
            contact="test",
            reachable_channel=ReachableChannel.SMS,
        )
        db.add(responder)
    incident = Incident(
        emergency_type=EmergencyType.FIRE,
        severity=Severity.CRITICAL,
        source_channel=ChannelKind.APP,
        status=IncidentStatus.ASSIGNED if assigned else IncidentStatus.REPORTED,
        latitude=5.56,
        longitude=-0.2,
    )
    db.add(incident)
    await db.flush()
    assignment = None
    if assigned:
        assignment = Assignment(
            incident_id=incident.id,
            responder_id=responder.id,
            hospital_id=hospital.id,
        )
        db.add(assignment)
    await db.commit()
    return incident, responder, hospital, assignment


async def transition(db, incident_id, status):
    return await advance_status(
        UUID(incident_id),
        StatusUpdate(status=status),
        request=None,
        _principal=None,
        session=db,
    )


async def test_resolution_releases_once_and_emits_one_event(db_session):
    incident, responder, hospital, assignment = await scenario(db_session)
    await transition(db_session, incident.id, IncidentStatus.RESOLVED)
    await transition(db_session, incident.id, IncidentStatus.RESOLVED)
    assert hospital.available_capacity == 1
    assert responder.status == ResponderStatus.AVAILABLE
    assert assignment.released_at is not None
    assert (await db_session.scalar(select(func.count()).select_from(EventOutbox))) == 1
    assert (await db_session.scalar(select(func.count()).select_from(EventLog))) == 1


async def test_old_resolution_cannot_release_a_reassigned_responder(db_session):
    first, responder, hospital, _ = await scenario(db_session)
    await transition(db_session, first.id, IncidentStatus.RESOLVED)
    responder.status = ResponderStatus.ASSIGNED
    await scenario(db_session, responder=responder)
    await transition(db_session, first.id, IncidentStatus.RESOLVED)
    assert responder.status == ResponderStatus.ASSIGNED
    assert hospital.available_capacity == 1


@pytest.mark.parametrize(
    "start,target",
    [
        (IncidentStatus.REPORTED, IncidentStatus.EN_ROUTE),
        (IncidentStatus.REPORTED, IncidentStatus.RESOLVED),
        (IncidentStatus.RESOLVED, IncidentStatus.EN_ROUTE),
    ],
)
async def test_invalid_source_transition_is_rejected(db_session, start, target):
    incident, _, _, _ = await scenario(db_session, assigned=False)
    incident.status = start
    await db_session.commit()
    with pytest.raises(HTTPException) as error:
        await transition(db_session, incident.id, target)
    assert error.value.status_code == 409
    assert incident.status == start
    assert (await db_session.scalar(select(func.count()).select_from(EventOutbox))) == 0


async def test_concurrent_resolution_preserves_single_release(session_factory):
    async with session_factory() as db:
        incident, _, hospital, _ = await scenario(db)
        incident_id, hospital_id = incident.id, hospital.id

    async def resolve():
        async with session_factory() as db:
            await transition(db, incident_id, IncidentStatus.RESOLVED)

    await asyncio.gather(*(resolve() for _ in range(12)))
    async with session_factory() as db:
        assert (await db.get(Hospital, hospital_id)).available_capacity == 1
        assert (await db.scalar(select(func.count()).select_from(EventLog))) == 1
        assert (await db.scalar(select(func.count()).select_from(EventOutbox))) == 1


async def test_concurrent_distinct_releases_do_not_lose_capacity(session_factory):
    async with session_factory() as db:
        first, _, hospital, _ = await scenario(db, capacity=2)
        second, _, _, _ = await scenario(db, hospital=hospital)
        incident_ids, hospital_id = [first.id, second.id], hospital.id

    async def resolve(incident_id):
        async with session_factory() as db:
            await transition(db, incident_id, IncidentStatus.RESOLVED)

    await asyncio.gather(*(resolve(incident_id) for incident_id in incident_ids))
    async with session_factory() as db:
        assert (await db.get(Hospital, hospital_id)).available_capacity == 2


async def test_concurrent_last_bed_reservations(session_factory):
    specialty = uuid.uuid4().hex
    async with session_factory() as db:
        hospital = Hospital(
            name="Last bed",
            latitude=5.56,
            longitude=-0.2,
            total_capacity=1,
            available_capacity=1,
            specialties=[specialty],
        )
        db.add(hospital)
        await db.commit()
        hospital_id = hospital.id

    async def reserve():
        async with session_factory() as db:
            result = await match_hospital(
                db,
                latitude=5.56,
                longitude=-0.2,
                required_specialty=specialty,
            )
            await db.commit()
            return result is not None

    results = await asyncio.gather(*(reserve() for _ in range(12)))
    assert sum(results) == 1
    async with session_factory() as db:
        assert (await db.get(Hospital, hospital_id)).available_capacity == 0


@pytest.mark.parametrize("total,available", [(1, -1), (1, 2), (-1, 0)])
async def test_database_rejects_invalid_capacity(db_session, total, available):
    db_session.add(
        Hospital(
            name="Invalid",
            latitude=0,
            longitude=0,
            total_capacity=total,
            available_capacity=available,
            specialties=[],
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_database_rejects_duplicate_incident_assignment(db_session):
    incident, _, _, _ = await scenario(db_session)
    other = Responder(
        name="Other",
        type=ResponderType.FIRE,
        status=ResponderStatus.ASSIGNED,
        contact="test",
        reachable_channel=ReachableChannel.SMS,
    )
    db_session.add(other)
    await db_session.flush()
    db_session.add(Assignment(incident_id=incident.id, responder_id=other.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_database_rejects_two_active_assignments_for_responder(db_session):
    _, responder, _, _ = await scenario(db_session)
    another, _, _, _ = await scenario(db_session, assigned=False)
    db_session.add(Assignment(incident_id=another.id, responder_id=responder.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


class FakeGeo:
    def __init__(self, ids):
        self.ids = ids
        self.radii = []

    async def geosearch(self, *args, **kwargs):
        self.radii.append(kwargs["radius"])
        await asyncio.sleep(0)
        return [
            [responder_id, index + 1] for index, responder_id in enumerate(self.ids)
        ]


def reported(incident):
    return IncidentReported(
        incident_id=incident.id,
        source_channel=incident.source_channel,
        emergency_type=incident.emergency_type,
        severity=incident.severity,
        latitude=incident.latitude,
        longitude=incident.longitude,
    )


async def test_concurrent_redelivery_creates_one_assignment(session_factory):
    async with session_factory() as db:
        incident, responder, _, _ = await scenario(db, assigned=False)
        payload, responder_id = reported(incident), responder.id
    geo = FakeGeo([responder_id])

    async def dispatch():
        async with session_factory() as db:
            return await dispatch_incident(db, geo, payload)

    results = await asyncio.gather(*(dispatch() for _ in range(8)))
    assert sum(result.assigned for result in results) == 1
    async with session_factory() as db:
        assert (await db.scalar(select(func.count()).select_from(Assignment))) == 1
        assert (await db.scalar(select(func.count()).select_from(EventOutbox))) == 1


async def test_dispatch_redelivery_cannot_reopen_resolved_incident(db_session):
    incident, responder, hospital, _ = await scenario(db_session)
    await transition(db_session, incident.id, IncidentStatus.RESOLVED)
    result = await dispatch_incident(
        db_session, FakeGeo([responder.id]), reported(incident)
    )
    assert not result.assigned and not result.retryable
    assert incident.status == IncidentStatus.RESOLVED
    assert responder.status == ResponderStatus.AVAILABLE
    assert hospital.available_capacity == 1


async def test_matching_does_not_lock_other_available_responders(session_factory):
    async with session_factory() as db:
        first, responder1, _, _ = await scenario(db, assigned=False)
        second, responder2, _, _ = await scenario(db, assigned=False)
        payloads = [reported(first), reported(second)]
        ids = [responder1.id, responder2.id]
    geo = FakeGeo(ids)

    async def dispatch(payload):
        async with session_factory() as db:
            return await dispatch_incident(db, geo, payload)

    results = await asyncio.gather(*(dispatch(payload) for payload in payloads))
    assert all(result.assigned for result in results)
    assert len({result.responder_id for result in results}) == 2


async def test_maximum_match_radius_is_queried():
    from dispatch.responder_matcher import match_nearest

    geo = FakeGeo([])
    result = await match_nearest(
        None,
        geo,
        emergency_type=EmergencyType.FIRE,
        latitude=0,
        longitude=0,
        start_radius_m=5000,
        max_radius_m=25000,
    )
    assert result is None
    assert geo.radii == [5000, 10000, 20000, 25000]


async def test_hospital_match_does_not_lock_unselected_hospitals(session_factory):
    specialty = uuid.uuid4().hex
    async with session_factory() as db:
        for latitude in (0, 0.01):
            db.add(
                Hospital(
                    name="Candidate",
                    latitude=latitude,
                    longitude=0,
                    total_capacity=1,
                    available_capacity=1,
                    specialties=[specialty],
                )
            )
        await db.commit()
    async with session_factory() as first, session_factory() as second:
        one = await match_hospital(
            first,
            latitude=0,
            longitude=0,
            required_specialty=specialty,
        )
        two = await match_hospital(
            second,
            latitude=0,
            longitude=0,
            required_specialty=specialty,
        )
        assert one is not None and two is not None
        assert one.hospital.id != two.hospital.id
        await first.commit()
        await second.commit()
