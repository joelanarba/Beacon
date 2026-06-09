"""Dispatcher incident endpoints: advancing status (authenticated) writes an
audit row, and the route is guarded.
"""

from __future__ import annotations

from sqlalchemy import select

from auth.security import hash_password
from channels.base import ChannelKind, EmergencyType, Severity
from models.db import EventLog, Incident, IncidentStatus, User, UserRole


async def _dispatcher_token(client, db_session, email):
    db_session.add(
        User(
            email=email,
            hashed_password=hash_password("pw12345"),
            role=UserRole.DISPATCHER,
        )
    )
    await db_session.commit()
    resp = await client.post(
        "/auth/login", json={"email": email, "password": "pw12345"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _incident(db_session, status):
    incident = Incident(
        reporter_contact="+233200000001",
        emergency_type=EmergencyType.FIRE,
        severity=Severity.CRITICAL,
        status=status,
        source_channel=ChannelKind.USSD,
        description="test",
    )
    db_session.add(incident)
    await db_session.commit()
    await db_session.refresh(incident)
    return incident


async def test_advance_status_to_en_route(client, db_session):
    token = await _dispatcher_token(client, db_session, "dispatch4a@test.local")
    incident = await _incident(db_session, IncidentStatus.ASSIGNED)

    resp = await client.post(
        f"/incidents/{incident.id}/status",
        json={"status": "EN_ROUTE"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "EN_ROUTE"

    logs = (
        (
            await db_session.execute(
                select(EventLog).where(EventLog.incident_id == incident.id)
            )
        )
        .scalars()
        .all()
    )
    assert any(e.event_type == "incident.en_route" for e in logs)


async def test_advance_status_rejects_invalid_target(client, db_session):
    token = await _dispatcher_token(client, db_session, "dispatch4b@test.local")
    incident = await _incident(db_session, IncidentStatus.REPORTED)

    resp = await client.post(
        f"/incidents/{incident.id}/status",
        json={"status": "REPORTED"},  # not an allowed transition target
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


async def test_incidents_requires_auth(client):
    assert (await client.get("/incidents")).status_code == 401
