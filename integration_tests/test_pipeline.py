"""Real HTTP -> PostgreSQL outbox -> RabbitMQ -> dispatch -> notification."""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

pytestmark = pytest.mark.integration


async def socket_event(socket, incident_id, kind):
    async with asyncio.timeout(45):
        while True:
            event = json.loads(await socket.recv())
            if event["incident_id"] == incident_id and event["type"] == kind:
                return event


@pytest.mark.parametrize("stack", [{"seed": False}], indirect=True)
async def test_clean_start_migrations_and_documented_seed(stack):
    await stack.start_app()
    for _ in range(2):
        stack.compose("exec", "-T", "app", "python", "seed.py")
    assert await stack.scalar("SELECT count(*) FROM responders") == 5
    assert await stack.scalar("SELECT count(*) FROM hospitals") == 3
    assert await stack.scalar("SELECT count(*) FROM users") == 1
    async with stack.redis() as rc:
        assert await rc.zcard("responders:live") == 5
        for row in await stack.sql("SELECT id FROM responders"):
            assert (await rc.geopos("responders:live", str(row["id"])))[0] is not None
    response = await stack.http(
        "POST",
        "/auth/login",
        json={"email": "dispatcher@example.com", "password": "beacon-dispatch"},
    )
    assert response.status_code == 200
    await stack.invariants()


async def test_complete_path_over_http_amqp_websocket_and_simulated_sms(stack):
    await stack.start_app()
    token = await stack.login()
    async with websockets.connect(
        stack.url.replace("http://", "ws://") + f"/ws/dispatch?token={token}"
    ) as socket:
        incident = await stack.ingest("complete-path")
        event = await socket_event(socket, incident, "incident.assigned")
        await stack.assigned(incident)
        messages = await stack.notified(incident)
        assignment = (
            await stack.sql(
                "SELECT * FROM assignments WHERE incident_id=$1::text::uuid", incident
            )
        )[0]
        assert str(assignment["responder_id"]) == event["responder_id"]
        assert str(assignment["hospital_id"]) == stack.hospital
        assert messages[0]["to"].startswith("synthetic-")
        assert await stack.scalar("SELECT available_capacity FROM hospitals") == 3
        await stack.status(incident, "EN_ROUTE", token)
        assert (await socket_event(socket, incident, "incident.status"))[
            "status"
        ] == "EN_ROUTE"
        await stack.status(incident, "RESOLVED", token)
        assert (await socket_event(socket, incident, "incident.status"))[
            "status"
        ] == "RESOLVED"
    await stack.idle()
    assert await stack.scalar("SELECT available_capacity FROM hospitals") == 4
    assert (
        await stack.scalar(
            "SELECT count(*) FROM event_log WHERE incident_id=$1::text::uuid", incident
        )
        == 4
    )
    assert await stack.scalar("SELECT count(*) FROM dead_letters") == 0
    await stack.invariants()


async def test_all_ingress_identities_and_concurrent_replays(stack):
    await stack.start_app()
    incidents = await asyncio.gather(
        *(stack.ingest("same-app-report") for _ in range(8))
    )
    assert len(set(incidents)) == 1
    assert await stack.ingest("same-app-report") == incidents[0]
    conflict = await stack.http(
        "POST",
        "/ingest/app",
        headers={"Idempotency-Key": "same-app-report"},
        json={"emergency_type": "MEDICAL", "description": "Changed synthetic content"},
    )
    assert conflict.status_code == 409

    sms = {
        "id": "synthetic-provider-id",
        "from": "synthetic-reporter",
        "text": "FIRE Osu",
    }
    first = await stack.http("POST", "/ingest/sms", data=sms)
    second = await stack.http("POST", "/ingest/sms", data=sms)
    assert first.status_code == second.status_code == 200
    assert first.text == second.text
    changed = await stack.http(
        "POST", "/ingest/sms", data={**sms, "text": "MED Changed"}
    )
    assert changed.status_code == 409

    form = {"sessionId": "synthetic-ussd-session", "phoneNumber": "synthetic-reporter"}
    for text in ("", "3", "3*Osu"):
        response = await stack.http("POST", "/ingest/ussd", data={**form, "text": text})
        assert response.status_code == 200 and response.text.startswith("CON ")
    final = {**form, "text": "3*Osu*1"}
    first = await stack.http("POST", "/ingest/ussd", data=final)
    second = await stack.http("POST", "/ingest/ussd", data=final)
    assert first.status_code == second.status_code == 200
    assert first.text == second.text and first.text.startswith("END Report received")
    async with stack.redis() as rc:
        assert not await rc.exists("ussd:session:synthetic-ussd-session")
    changed = await stack.http(
        "POST", "/ingest/ussd", data={**final, "phoneNumber": "other-reporter"}
    )
    assert changed.status_code == 409

    # Identical content without an identity represents distinct emergency reports.
    assert await stack.ingest() != await stack.ingest()
    for _ in range(2):
        assert (
            await stack.http(
                "POST", "/ingest/sms", data={k: v for k, v in sms.items() if k != "id"}
            )
        ).status_code == 200
    rows = await stack.sql("SELECT id FROM incidents")
    assert len(rows) == 7
    for row in rows:
        incident = str(row["id"])
        await stack.assigned(incident)
        await stack.notified(incident)
    await stack.idle()
    assert await stack.scalar("SELECT count(*) FROM assignments") == 7
    assert (
        await stack.scalar(
            "SELECT count(*) FROM event_outbox "
            "WHERE routing_key='incident.reported' AND delivery_attempt=0"
        )
        == 7
    )
    assert await stack.scalar("SELECT count(*) FROM dead_letters") == 0
    await stack.invariants()


async def test_duplicate_delivery_before_assignment_and_after_resolution(stack):
    await stack.start_app()
    await stack.arm("before_process")
    incident = await stack.ingest("broker-duplicates")
    assert (await stack.barrier())["event_id"] == f"incident.reported:{incident}"
    assert await stack.scalar("SELECT count(*) FROM assignments") == 0
    event_id, payload = await stack.event(incident)
    for _ in range(3):
        await stack.publish("incident.reported", payload, event_id)
    await stack.control("/release", {})
    await stack.assigned(incident)
    await stack.notified(incident)
    await stack.idle()
    assert await stack.scalar("SELECT count(*) FROM assignments") == 1
    assert await stack.scalar("SELECT available_capacity FROM hospitals") == 3

    # External notification side effects intentionally have at-least-once semantics.
    assigned_id, assigned = await stack.event(incident, "incident.assigned")
    for _ in range(2):
        await stack.publish("incident.assigned", assigned, assigned_id)

    async def three_notifications():
        return len(await stack.sms()) >= 3

    await stack.wait(
        three_notifications, description="observable duplicate simulated SMS"
    )
    stack.run.record(
        {"incident": incident, "simulated_sms_copies": len(await stack.sms())}
    )

    token = await stack.login()
    await stack.status(incident, "RESOLVED", token)
    for _ in range(3):
        await stack.publish("incident.reported", payload, event_id)
    await stack.idle()
    assert await stack.scalar("SELECT status FROM incidents") == "RESOLVED"
    assert await stack.scalar("SELECT count(*) FROM assignments") == 1
    assert await stack.scalar("SELECT available_capacity FROM hospitals") == 4
    await stack.invariants()


async def test_concurrent_resolution_and_old_resolution_after_reassignment(stack):
    # One eligible ambulance makes reassignment deterministic.
    await stack.sql(
        """UPDATE responders SET status='OFFLINE' WHERE type='AMBULANCE'
        AND id <> (SELECT id FROM responders WHERE type='AMBULANCE'
                   ORDER BY id LIMIT 1)"""
    )
    await stack.start_app()
    token = await stack.login()
    first = await stack.ingest("first-assignment")
    await stack.assigned(first)
    await asyncio.gather(*(stack.status(first, "RESOLVED", token) for _ in range(12)))
    assert await stack.scalar("SELECT available_capacity FROM hospitals") == 4
    assert (
        await stack.scalar(
            "SELECT count(*) FROM event_log WHERE event_type='incident.resolved'"
        )
        == 1
    )
    second = await stack.ingest("second-assignment")
    await stack.assigned(second)
    await stack.status(first, "RESOLVED", token)
    assert (
        await stack.scalar("SELECT count(*) FROM responders WHERE status='ASSIGNED'")
        == 1
    )
    assert await stack.scalar("SELECT available_capacity FROM hospitals") == 3
    await stack.status(second, "RESOLVED", token)
    await stack.idle()
    assert await stack.scalar("SELECT available_capacity FROM hospitals") == 4
    await stack.invariants()


async def test_distinct_concurrent_releases_share_hospital(stack):
    await stack.start_app()
    incidents = await asyncio.gather(*(stack.ingest(f"distinct-{i}") for i in range(4)))
    for incident in incidents:
        await stack.assigned(incident)
    assert await stack.scalar("SELECT available_capacity FROM hospitals") == 0
    token = await stack.login()
    await asyncio.gather(
        *(stack.status(incident, "RESOLVED", token) for incident in incidents)
    )
    assert await stack.scalar("SELECT available_capacity FROM hospitals") == 4
    assert (
        await stack.scalar(
            "SELECT count(*) FROM assignments WHERE released_at IS NOT NULL"
        )
        == 4
    )
    await stack.idle()
    await stack.invariants()


async def test_last_bed_contention_never_overbooks(stack):
    await stack.sql("UPDATE hospitals SET total_capacity=1,available_capacity=1")
    await stack.start_app()
    incidents = await asyncio.gather(*(stack.ingest(f"last-bed-{i}") for i in range(8)))

    async def attempts_seen():
        return (
            await stack.scalar(
                "SELECT count(DISTINCT event_id) FROM event_outbox "
                "WHERE delivery_attempt > 0"
            )
            == 7
        )

    await stack.wait(
        attempts_seen, description="all losing reservations durably retried"
    )
    assert await stack.scalar("SELECT count(*) FROM assignments") == 1
    assert await stack.scalar("SELECT available_capacity FROM hospitals") == 0
    assigned = await stack.scalar("SELECT incident_id::text FROM assignments")
    assert assigned in incidents
    await stack.invariants()
