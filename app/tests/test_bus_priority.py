"""Check consumer restoration and priority dequeue order.

Restoration uses the pinned library with broker I/O replaced; priority ordering
uses a real broker. Neither establishes priority-specific latency.
"""

from __future__ import annotations

import asyncio
import gc
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aio_pika.robust_channel import RobustChannel
from channels.base import ChannelKind, EmergencyType, Severity
from config import get_settings
from dispatch.engine import start_dispatch_consumer
from events.bus import EventBus
from events.schemas import IncidentReported
from notification.notifier import start_notification_consumer


async def test_consumers_remain_registered_for_robust_channel_restore(monkeypatch):
    # Use the pinned library's real weak queue registry and restore path; only
    # broker I/O is replaced. Consumer callbacks must survive caller scope exit.
    channel = RobustChannel(SimpleNamespace())
    channel.default_exchange = SimpleNamespace(channel=channel)
    transport = SimpleNamespace(
        queue_declare=AsyncMock(
            side_effect=lambda **kwargs: SimpleNamespace(queue=kwargs["queue"])
        ),
        queue_bind=AsyncMock(),
        basic_consume=AsyncMock(return_value=SimpleNamespace(consumer_tag="test")),
        basic_qos=AsyncMock(),
    )
    monkeypatch.setattr(channel, "ready", AsyncMock())
    monkeypatch.setattr(
        channel, "get_underlay_channel", AsyncMock(return_value=transport)
    )
    bus = EventBus("unused", "beacon.events", 10)
    bus._channel, bus._exchange = channel, "beacon.events"
    await start_dispatch_consumer(bus)
    await start_notification_consumer(bus, object())
    assert transport.basic_consume.await_count == 2

    gc.collect()
    transport.basic_consume.reset_mock()
    await channel._on_open()
    assert sorted(
        call.kwargs["queue"] for call in transport.basic_consume.await_args_list
    ) == ["dispatch.incoming", "notify.outgoing"]

    await bus.close()
    gc.collect()
    assert not any(channel._queues.values())


def _payload(severity: Severity) -> IncidentReported:
    return IncidentReported(
        incident_id=str(uuid.uuid4()),
        source_channel=ChannelKind.USSD,
        emergency_type=EmergencyType.FIRE,
        severity=severity,
    )


async def _get_one(queue, attempts: int = 50):
    """Pull one message, tolerating the brief routing delay after publish."""
    for _ in range(attempts):
        message = await queue.get(fail=False)
        if message is not None:
            return message
        await asyncio.sleep(0.02)
    raise AssertionError("expected a message but the queue stayed empty")


@pytest.mark.rabbitmq
async def test_critical_dequeued_before_waiting_standard():
    settings = get_settings()
    bus = EventBus(
        settings.amqp_url, settings.event_exchange, settings.max_queue_priority
    )
    await bus.connect()

    # An isolated routing key so a running app's dispatch queue never competes
    # for these test messages on the shared exchange.
    routing_key = f"test.priority.{uuid.uuid4().hex}"
    queue = await bus.declare_queue(
        f"test.prio.{uuid.uuid4().hex}",
        [routing_key],
        durable=False,
        auto_delete=True,
    )
    try:
        # STANDARD enqueued first; CRITICAL second. Both sit in the queue before
        # we pull, so priority ordering - not arrival order - decides.
        await bus.publish(
            routing_key,
            _payload(Severity.STANDARD),
            priority=Severity.STANDARD.numeric_priority(),
        )
        await bus.publish(
            routing_key,
            _payload(Severity.CRITICAL),
            priority=Severity.CRITICAL.numeric_priority(),
        )

        first = await _get_one(queue)
        first_payload = IncidentReported.model_validate_json(first.body)
        await first.ack()

        second = await _get_one(queue)
        second_payload = IncidentReported.model_validate_json(second.body)
        await second.ack()
    finally:
        try:
            await queue.delete(if_unused=False, if_empty=False)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        await bus.close()

    assert first_payload.severity is Severity.CRITICAL  # jumped ahead
    assert second_payload.severity is Severity.STANDARD
