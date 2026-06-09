"""Ingestion endpoints (DB + Redis): the three channels converge to a persisted
``REPORTED`` incident, and the USSD session lifecycle (complete / timeout) is
honoured. These run inside the container where Postgres + Redis are reachable.
"""

from __future__ import annotations

import uuid

from pytest import approx
from sqlalchemy import func, select

from models.db import Incident
from sim.provider import OUTBOX_KEY, SimulatedSmsProvider, read_outbox
from utils.redis import make_redis


async def _count_incidents(session) -> int:
    result = await session.execute(select(func.count()).select_from(Incident))
    return result.scalar_one()


def _ussd_form(sid: str, phone: str, text: str) -> dict:
    return {"sessionId": sid, "phoneNumber": phone, "text": text}


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
async def test_ingest_app_persists_incident(client, db_session):
    resp = await client.post(
        "/ingest/app",
        json={
            "emergency_type": "MEDICAL",
            "description": "cardiac arrest",
            "reporter_contact": "user-1",
            "latitude": 5.56,
            "longitude": -0.2,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "REPORTED"
    assert body["source_channel"] == "APP"

    row = (
        await db_session.execute(
            select(Incident).where(Incident.id == body["incident_id"])
        )
    ).scalar_one()
    assert row.source_channel.value == "APP"
    assert row.latitude == approx(5.56)


# --------------------------------------------------------------------------- #
# SMS
# --------------------------------------------------------------------------- #
async def test_ingest_sms_keyword_persists(client, db_session):
    before = await _count_incidents(db_session)
    resp = await client.post(
        "/ingest/sms", data={"from": "+233200000222", "text": "FIRE Osu market"}
    )
    assert resp.status_code == 200
    assert "Ref:" in resp.text
    assert await _count_incidents(db_session) == before + 1


async def test_ingest_sms_malformed_persists_nothing(client, db_session):
    before = await _count_incidents(db_session)
    resp = await client.post(
        "/ingest/sms", data={"from": "+233200000222", "text": "please help me"}
    )
    assert resp.status_code == 200
    assert "FIRE" in resp.text  # help reply, not an acknowledgement
    assert await _count_incidents(db_session) == before


# --------------------------------------------------------------------------- #
# USSD — full walk + timeout
# --------------------------------------------------------------------------- #
async def test_ingest_ussd_full_walk_persists(client, db_session):
    sid = "test-" + uuid.uuid4().hex
    phone = "+233200000333"

    r0 = await client.post("/ingest/ussd", data=_ussd_form(sid, phone, ""))
    assert r0.text.startswith("CON ")
    r1 = await client.post("/ingest/ussd", data=_ussd_form(sid, phone, "2"))
    assert r1.text.startswith("CON ")
    r2 = await client.post("/ingest/ussd", data=_ussd_form(sid, phone, "2*Tema"))
    assert "Confirm" in r2.text
    r3 = await client.post("/ingest/ussd", data=_ussd_form(sid, phone, "2*Tema*1"))
    assert r3.text.startswith("END ") and "Ref:" in r3.text

    row = (
        (
            await db_session.execute(
                select(Incident).where(Incident.reporter_contact == phone)
            )
        )
        .scalars()
        .first()
    )
    assert row is not None
    assert row.emergency_type.value == "FIRE"
    assert row.area_label == "Tema"
    assert row.source_channel.value == "USSD"


async def test_ingest_ussd_timeout_after_session_expiry(client, db_session):
    sid = "test-" + uuid.uuid4().hex
    phone = "+233200000444"

    r0 = await client.post("/ingest/ussd", data=_ussd_form(sid, phone, ""))
    assert r0.text.startswith("CON ")

    # Simulate the session window elapsing between callbacks: drop the liveness
    # key Redis was holding under its TTL.
    rc = make_redis()
    await rc.delete(f"ussd:session:{sid}")
    await rc.aclose()

    r1 = await client.post("/ingest/ussd", data=_ussd_form(sid, phone, "2"))
    assert r1.text.startswith("END ")
    assert "expired" in r1.text.lower()


# --------------------------------------------------------------------------- #
# Simulator outbound sink
# --------------------------------------------------------------------------- #
async def test_simulated_provider_records_to_outbox():
    rc = make_redis()
    await rc.delete(OUTBOX_KEY)
    await SimulatedSmsProvider(rc).send_sms("+233200000999", "Responder assigned")
    messages = await read_outbox(rc, 10)
    await rc.aclose()

    assert messages[0]["to"] == "+233200000999"
    assert messages[0]["message"] == "Responder assigned"


async def test_sim_harness_is_served(client):
    resp = await client.get("/sim/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Beacon" in resp.text
