"""Fault injection against owned containers, with deterministic crash boundaries."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


async def has_retry(stack, event_id):
    return (
        await stack.scalar(
            "SELECT count(*) FROM event_outbox "
            "WHERE event_id=$1 AND delivery_attempt > 0",
            event_id,
        )
        > 0
    )


@pytest.mark.parametrize("stack", [{"broker": False}], indirect=True)
async def test_initial_broker_outage_and_worker_restart_preserve_accepted_work(stack):
    await stack.start_app(ready=False)
    await stack.wait_http("/ready", 503)
    incident = await stack.ingest("accepted-without-broker")
    assert await stack.scalar("SELECT published_at FROM event_outbox") is None
    assert await stack.scalar("SELECT count(*) FROM assignments") == 0
    stack.fault("kill", "app")
    await stack.start_app(ready=False)
    await stack.wait_http("/ready", 503)
    await stack.recover("rabbitmq")
    await stack.assigned(incident)
    await stack.notified(incident)
    await stack.idle()
    assert await stack.scalar("SELECT count(*) FROM assignments") == 1
    await stack.invariants()


async def test_broker_reconnection_with_an_inflight_delivery(stack):
    await stack.start_app()
    await stack.arm("before_process")
    first = await stack.ingest("inflight-at-broker-stop")
    await stack.barrier()
    stack.fault("stop", "rabbitmq")
    await stack.wait_http("/ready", 503)
    second = await stack.ingest("accepted-during-broker-stop")
    assert (
        await stack.scalar(
            "SELECT count(*) FROM event_outbox WHERE published_at IS NULL"
        )
        >= 1
    )
    await stack.recover("rabbitmq")
    await stack.control("/release", {})
    for incident in (first, second):
        await stack.assigned(incident)
        await stack.notified(incident)
    await stack.idle()
    assert await stack.scalar("SELECT count(*) FROM assignments") == 2
    await stack.invariants()


async def test_worker_kill_after_assignment_before_ack(stack):
    await stack.start_app()
    await stack.arm("before_ack")
    incident = await stack.ingest("kill-before-ack")
    await stack.barrier()
    assert await stack.scalar("SELECT count(*) FROM assignments") == 1

    async def inflight():
        return any(
            q["name"] == "dispatch.incoming"
            and q.get("messages_unacknowledged", 0) >= 1
            for q in await stack.queues()
        )

    await stack.wait(inflight, description="broker owns unacknowledged report")
    stack.fault("kill", "app")
    await stack.recover("app")
    await stack.notified(incident)
    await stack.idle()
    assert await stack.scalar("SELECT count(*) FROM assignments") == 1
    assert await stack.scalar("SELECT available_capacity FROM hospitals") == 3
    await stack.invariants()


async def test_worker_kill_after_confirm_before_outbox_commit_republishes(stack):
    await stack.start_app()
    async with stack.amqp() as (channel, exchange):
        observer = await channel.declare_queue(exclusive=True, auto_delete=True)
        await observer.bind(exchange, "incident.reported")
        await stack.arm("after_publish")
        incident = await stack.ingest("kill-after-confirm")
        barrier = await stack.barrier()
        assert barrier["event_id"] == f"incident.reported:{incident}"
        assert (
            await stack.scalar(
                "SELECT published_at FROM event_outbox WHERE event_id=$1",
                barrier["event_id"],
            )
            is None
        )
        first = await observer.get(timeout=10)
        await first.ack()
        stack.fault("kill", "app")
        await stack.recover("app")

        async def republished():
            return await observer.get(fail=False, timeout=5)

        second = await stack.wait(
            republished, description="confirmed event republished after rollback"
        )
        await second.ack()
        assert first.message_id == second.message_id == barrier["event_id"]
        stack.run.record(
            {"event_id": first.message_id, "confirmed_publications_observed": 2}
        )
    await stack.assigned(incident)
    await stack.notified(incident)
    await stack.idle()
    assert await stack.scalar("SELECT count(*) FROM assignments") == 1
    await stack.invariants()


async def test_real_redis_outage_dispatch_retry_recovers(stack):
    await stack.start_app()
    stack.fault("stop", "redis")
    await stack.wait_http("/ready", 503)
    incident = await stack.ingest("redis-dispatch-outage")
    await stack.wait(
        lambda: has_retry(stack, f"incident.reported:{incident}"),
        description="durable dispatch retry during Redis outage",
    )
    await stack.recover("redis")
    await stack.assigned(incident)
    await stack.notified(incident)
    await stack.idle()
    assert await stack.scalar("SELECT count(*) FROM dead_letters") == 0
    await stack.invariants()


async def test_simulated_sms_failure_uses_durable_notification_retry(stack):
    await stack.start_app()
    await stack.arm("before_process", "incident.assigned")
    incident = await stack.ingest("redis-notification-outage")
    await stack.barrier()
    stack.fault("stop", "redis")
    await stack.control("/release", {})
    await stack.wait(
        lambda: has_retry(stack, f"incident.assigned:{incident}"),
        description="durable notification retry",
    )
    await stack.recover("redis")
    await stack.notified(incident)
    await stack.idle()
    assert await stack.scalar("SELECT count(*) FROM assignments") == 1
    assert await stack.scalar("SELECT count(*) FROM dead_letters") == 0
    await stack.invariants()


async def test_postgres_failure_during_retry_handoff_retains_broker_work(stack):
    await stack.start_app()
    await stack.arm("before_failure_store")
    stack.fault("stop", "redis")
    incident = await stack.ingest("database-handoff-outage")
    await stack.barrier()
    stack.fault("stop", "postgres")
    await stack.control("/release", {})

    async def handoff_failed():
        return (
            "delivery.handoff_failed"
            in stack.compose("logs", "--no-color", "app").stdout
        )

    await stack.wait(handoff_failed, description="real database handoff failure")

    async def retained_by_broker():
        return any(
            q["name"] == "dispatch.incoming" and q["messages"] >= 1
            for q in await stack.queues()
        )

    await stack.wait(retained_by_broker, description="broker retains failed handoff")
    await stack.recover("postgres", ready=False)
    await stack.recover("redis")
    await stack.assigned(incident)
    await stack.notified(incident)
    await stack.idle()
    assert await stack.scalar("SELECT count(*) FROM assignments") == 1
    await stack.invariants()


async def test_default_retry_schedule_exhausts_into_database_dead_letter(stack):
    await stack.sql("UPDATE responders SET status='OFFLINE'")
    await stack.start_app()
    incident = await stack.ingest("exhausted-resources")
    event_id = f"incident.reported:{incident}"

    async def terminal():
        return await stack.sql("SELECT * FROM dead_letters WHERE event_id=$1", event_id)

    dead = (
        await stack.wait(
            terminal, timeout=120, description="five default retries exhausted"
        )
    )[0]
    await stack.idle()
    assert dead["consumer"] == "dispatch" and dead["delivery_attempt"] == 5
    assert dead["error_type"] == "RetryableDeliveryError"
    rows = await stack.sql(
        """SELECT delivery_attempt,published_at,
        EXTRACT(EPOCH FROM (available_at-created_at)) AS delay FROM event_outbox
        WHERE event_id=$1 ORDER BY delivery_attempt""",
        event_id,
    )
    assert [r["delivery_attempt"] for r in rows] == list(range(6))
    assert all(r["published_at"] is not None for r in rows)
    for row, seconds in zip(rows[1:], (1, 2, 4, 8, 16), strict=True):
        assert float(row["delay"]) >= seconds - 0.2
    assert await stack.scalar("SELECT count(*) FROM assignments") == 0
    await stack.invariants()


async def test_poison_messages_are_retained_once_and_worker_continues(stack):
    await stack.start_app()
    cases = [
        (b"not-json", 0),
        ({"incident_id": "missing-required-fields"}, 0),
        ({"incident_id": "invalid-attempt-header"}, "invalid"),
    ]
    for payload, attempt in cases:
        event_id = "synthetic-poison:" + uuid.uuid4().hex
        for _ in range(2):
            await stack.publish("incident.reported", payload, event_id, attempt=attempt)

    async def quarantined():
        return await stack.scalar("SELECT count(*) FROM dead_letters") == 3

    await stack.wait(quarantined, description="deduplicated poison dead letters")
    assert await stack.scalar("SELECT count(*) FROM event_outbox") == 0
    assert (
        await stack.scalar(
            "SELECT count(*) FROM dead_letters WHERE octet_length(body)>0"
        )
        == 3
    )
    incident = await stack.ingest("valid-after-poison")
    await stack.assigned(incident)
    await stack.notified(incident)
    await stack.idle()
    assert await stack.scalar("SELECT count(*) FROM dead_letters") == 3
    await stack.invariants()
