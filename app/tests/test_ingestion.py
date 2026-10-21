"""Ingestion endpoints (DB + Redis): the three channels converge to a persisted
``REPORTED`` incident, and the USSD session lifecycle (complete / timeout) is
honoured. These run inside the container where Postgres + Redis are reachable.
"""

from __future__ import annotations

import uuid

import pytest
from pytest import approx
from sqlalchemy import func, select

from config import get_settings
from models.db import EventOutbox, Incident
from sim.provider import SimulatedSmsProvider, read_outbox
from utils.redis import make_redis
from utils.redis import get_redis


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


async def test_ingest_secret_rejects_missing_header(client, monkeypatch):
    monkeypatch.setenv("INGEST_SHARED_SECRET", "provider-secret")
    get_settings.cache_clear()
    try:
        resp = await client.post(
            "/ingest/app",
            json={
                "emergency_type": "MEDICAL",
                "description": "cardiac arrest",
            },
        )
        assert resp.status_code == 401
    finally:
        get_settings.cache_clear()


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
# USSD - full walk + timeout
# --------------------------------------------------------------------------- #
@pytest.mark.redis
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


@pytest.mark.redis
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
@pytest.mark.redis
async def test_simulated_provider_records_to_outbox():
    rc = make_redis()
    key = f"test:sms:{uuid.uuid4().hex}"
    try:
        await SimulatedSmsProvider(rc, outbox_key=key).send_sms(
            "+233200000999", "Responder assigned"
        )
        messages = await read_outbox(rc, 10, outbox_key=key)
    finally:
        await rc.delete(key)
        await rc.aclose()

    assert messages[0]["to"] == "+233200000999"
    assert messages[0]["message"] == "Responder assigned"


async def test_sim_harness_is_served(client):
    resp = await client.get("/sim/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Beacon" in resp.text


async def test_app_idempotency_replays_ack_and_rejects_changed_content(
    client, db_session
):
    headers = {"Idempotency-Key": "app-retry"}
    body = {"emergency_type": "FIRE", "description": "Same report"}
    first = await client.post("/ingest/app", json=body, headers=headers)
    replay = await client.post("/ingest/app", json=body, headers=headers)
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    changed = await client.post(
        "/ingest/app", json={**body, "description": "Different report"}, headers=headers
    )
    assert changed.status_code == 409
    assert await _count_incidents(db_session) == 1
    assert await db_session.scalar(select(func.count()).select_from(EventOutbox)) == 1


async def test_sms_provider_message_id_deduplicates_callback(client, db_session):
    form = {"id": "provider-message-1", "from": "+233200000222", "text": "FIRE Osu"}
    first = await client.post("/ingest/sms", data=form)
    replay = await client.post("/ingest/sms", data=form)
    assert first.status_code == replay.status_code == 200
    assert first.text == replay.text
    assert await _count_incidents(db_session) == 1


async def test_ussd_terminal_callback_replays_after_session_cleanup(client, db_session):
    from main import app

    class SessionRedis:
        def __init__(self):
            self.keys = set()

        async def set(self, key, *args, **kwargs):
            self.keys.add(key)

        async def expire(self, key, *args):
            return key in self.keys

        async def delete(self, key):
            self.keys.discard(key)

    rc = SessionRedis()

    async def override_redis():
        yield rc

    app.dependency_overrides[get_redis] = override_redis
    form = _ussd_form("stable-session", "+233200000333", "")
    await client.post("/ingest/ussd", data=form)
    form["text"] = "2*Osu*1"
    first = await client.post("/ingest/ussd", data=form)
    assert not rc.keys
    replay = await client.post("/ingest/ussd", data=form)
    assert first.status_code == replay.status_code == 200
    assert first.text == replay.text
    assert await _count_incidents(db_session) == 1
    changed_phone = await client.post(
        "/ingest/ussd", data={**form, "phoneNumber": "+233200000444"}
    )
    assert changed_phone.status_code == 409
