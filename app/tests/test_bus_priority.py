"""Priority proof: a CRITICAL message is dequeued before an already-waiting
STANDARD one. This is the whole reason RabbitMQ priority queues were chosen for
dispatch. Runs against the real broker (inside the container).
"""

from __future__ import annotations

import asyncio
import uuid

from channels.base import ChannelKind, EmergencyType, Severity
from config import get_settings
from events.bus import EventBus
from events.schemas import IncidentReported


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
        # we pull, so priority ordering — not arrival order — decides.
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
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
        await bus.close()

    assert first_payload.severity is Severity.CRITICAL  # jumped ahead
    assert second_payload.severity is Severity.STANDARD
