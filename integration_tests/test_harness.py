"""Local checks for destructive-action guards and the test-only fault controls.

These tests use no Docker and are not real-service integration evidence.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml

from integration_tests.stack import (
    COMPOSE,
    ROOT,
    DockerRun,
    Stack,
    check_project,
    isolated_env,
    loopback_port,
    redact,
)
from integration_tests.worker_entry import Barrier, install_barriers, make_control


@pytest.mark.parametrize(
    "project", ["beacon", "main", "beacon-it-../../data", "", "beacon-it-short"]
)
def test_rejects_non_generated_project_names(project):
    with pytest.raises(ValueError):
        check_project(project)


@pytest.mark.parametrize(
    "binding",
    ["0.0.0.0:5432", "[::]:5432", "example.com:5432", "127.0.0.1:0", "127.0.0.1:65536"],
)
def test_rejects_external_or_invalid_port_bindings(binding):
    with pytest.raises(ValueError):
        loopback_port(binding)


def test_inherited_compose_and_operational_test_overrides_are_removed(monkeypatch):
    monkeypatch.setenv("COMPOSE_FILE", "development.yml")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "production")
    monkeypatch.setenv("BEACON_IT_IMAGE", "unexpected")
    monkeypatch.setenv("BEACON_TEST_DATABASE_URL", "operational-database")
    env = isolated_env("generated", "synthetic-image")
    assert not any(k.startswith(("COMPOSE_", "BEACON_TEST_")) for k in env)
    assert env["BEACON_IT_IMAGE"] == "synthetic-image"


@pytest.mark.parametrize("resource_kind", ["container", "volume", "network"])
def test_cleanup_refuses_resources_without_ownership_label(
    tmp_path, monkeypatch, resource_kind
):
    run = DockerRun(tmp_path)
    stack = Stack(run)
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        if "inspect" in args:
            return SimpleNamespace(
                stdout=json.dumps([{"Config": {"Labels": {}}, "Labels": {}}])
            )
        selected = (
            resource_kind == "container"
            and args[1] == "ps"
            or resource_kind == "volume"
            and args[1:3] == ["volume", "ls"]
            or resource_kind == "network"
            and args[1:3] == ["network", "ls"]
        )
        return SimpleNamespace(stdout="synthetic-resource\n" if selected else "")

    monkeypatch.setattr(run, "run", command)
    with pytest.raises(RuntimeError, match="unowned"):
        stack.cleanup()
    assert not any("down" in cmd for cmd in calls)


def test_compose_is_standalone_and_only_publishes_loopback_ports():
    config = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    for service in config["services"].values():
        assert "container_name" not in service
        assert "env_file" not in service
        for port in service.get("ports", []):
            assert port.startswith("127.0.0.1::")
        for volume in service.get("volumes", []):
            assert volume.split(":")[0] in config["volumes"]
    assert all(not v.get("external") for v in config["volumes"].values())
    assert all(not v.get("external") for v in config["networks"].values())
    app = config["services"]["app"]
    assert "beacon_test_e2e" in app["environment"]["DATABASE_URL"]
    assert app["environment"]["ENVIRONMENT"] == "test"
    assert "docker.sock" not in COMPOSE.read_text(encoding="utf-8")
    assert "integration-entry" not in (ROOT / "app" / "Dockerfile").read_text()


def test_evidence_redacts_jwts_and_connection_passwords():
    value = "eyJheader.payload.signature postgresql+asyncpg://user:sensitive@host/test"
    output = redact(value)
    assert "sensitive" not in output and "eyJheader" not in output


@pytest.mark.parametrize(
    "service,bindings",
    [
        ("postgres", {"pg": (5432, 41001)}),
        ("redis", {"redis": (6379, 41002)}),
        ("rabbitmq", {"amqp": (5672, 41003), "management": (15672, 41004)}),
        ("app", {"http": (8000, 41005), "control": (9010, 41006)}),
    ],
)
@pytest.mark.parametrize("readiness", [True, False])
async def test_recovery_refreshes_changed_ports_before_readiness(
    tmp_path, monkeypatch, service, bindings, readiness
):
    stack = Stack(DockerRun(tmp_path))
    stack.ports.update({key: 32000 for key in bindings})
    monkeypatch.setattr(stack, "verify_ownership", lambda: None)
    monkeypatch.setattr(stack, "compose", lambda *args, **kwargs: None)
    published = {internal: port for internal, port in bindings.values()}
    monkeypatch.setattr(stack, "port", lambda name, internal: published[internal])

    expected = {key: port for key, (_, port) in bindings.items()}
    checks = []

    async def ready(*args):
        checks.append(args)
        assert {key: stack.ports[key] for key in bindings} == expected

    monkeypatch.setattr(stack, "wait_http", ready)
    await stack.recover(service, ready=readiness)
    assert {key: stack.ports[key] for key in bindings} == expected
    assert checks == ([("/ready", 200)] if readiness else [])


async def test_fault_control_releases_all_held_deliveries_and_rearms():
    barrier = Barrier()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=make_control(barrier)),
        base_url="http://test-control",
    ) as client:
        assert (
            await client.post("/arm", json={"stage": "before_ack"})
        ).status_code == 200
        first = asyncio.create_task(
            barrier.hit("before_ack", "incident.reported", "event-one")
        )
        second = asyncio.create_task(
            barrier.hit("before_ack", "incident.reported", "event-two")
        )
        try:
            await asyncio.sleep(0)
            state = (await client.get("/state")).json()
            assert state["reached"] and not first.done() and not second.done()
            assert (
                await client.post("/arm", json={"stage": "after_publish"})
            ).status_code == 409
            await client.post("/release")
            await asyncio.wait_for(asyncio.gather(first, second), 2)
            assert (
                await client.post("/arm", json={"stage": "after_publish"})
            ).status_code == 200
            assert not (await client.get("/state")).json()["reached"]
        finally:
            barrier.release.set()
            await asyncio.gather(first, second)


async def test_fault_control_rejects_unknown_boundary_and_ignores_other_routes():
    barrier = Barrier()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=make_control(barrier)),
        base_url="http://test-control",
    ) as client:
        assert (
            await client.post("/arm", json={"stage": "unrelated"})
        ).status_code == 422
        await client.post("/arm", json={"stage": "before_ack"})
        await asyncio.wait_for(
            barrier.hit("before_ack", "incident.assigned", "other"), 1
        )
        assert not barrier.reached


@pytest.mark.parametrize(
    "stage", ["after_publish", "before_ack", "before_failure_store", "before_process"]
)
async def test_instrumentation_holds_the_intended_side_of_the_boundary(
    stage, monkeypatch
):
    # Import domain modules with unusable test URLs; never read operational settings.
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://unused@127.0.0.1:1/beacon_test"
    )
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("SECRET_KEY", "unused-test-only-signing-key")
    from aio_pika.message import IncomingMessage

    from events import outbox
    from events.bus import EventBus

    original = AsyncMock()
    monkeypatch.setattr(EventBus, "publish", original)
    monkeypatch.setattr(IncomingMessage, "ack", original)
    monkeypatch.setattr(outbox, "record_failure", original)
    monkeypatch.setattr(outbox, "consume_reliably", original)
    barrier = Barrier(stage=stage, routing_key="incident.reported")
    install_barriers(barrier)
    message = SimpleNamespace(
        routing_key="incident.reported", message_id="synthetic-event"
    )
    operations = {
        "after_publish": lambda: EventBus.publish(
            object(), "incident.reported", {}, message_id="synthetic-event"
        ),
        "before_ack": lambda: IncomingMessage.ack(message),
        "before_failure_store": lambda: outbox.record_failure(
            object(), message, "dispatch", RuntimeError(), 0
        ),
        "before_process": lambda: outbox.consume_reliably(
            message, "dispatch", AsyncMock()
        ),
    }
    task = asyncio.create_task(operations[stage]())
    try:
        await asyncio.sleep(0)
        assert barrier.reached and barrier.event_id == "synthetic-event"
        assert not task.done()
        assert original.await_count == (1 if stage == "after_publish" else 0)
    finally:
        barrier.release.set()
        await asyncio.wait_for(task, 2)
    assert original.await_count == 1
