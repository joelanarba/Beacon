"""Channel-aware notification fan-out + its bus consumer.

Beacon reaches each recipient on a channel they can actually use, instead of
assuming everyone has data. Fan-out is keyed on each party's reach:

- dispatchers sit at a data console        → WebSocket hub
- an app responder                          → WebSocket hub (responder role)
- a feature-phone responder (reachable=SMS) → SMS-out via the provider sink

The consumer binds ``incident.assigned`` (→ notify the responder + dispatchers)
and the status routing keys ``incident.en_route`` / ``incident.resolved``
(→ propagate to dispatchers).
"""

from __future__ import annotations

import aio_pika

from events.bus import EventBus
from events.schemas import (
    RK_ASSIGNED,
    RK_EN_ROUTE,
    RK_RESOLVED,
    IncidentAssigned,
    IncidentStatusChanged,
)
from models.db import ReachableChannel
from realtime.hub import ROLE_DISPATCHER, ROLE_RESPONDER, ConnectionHub
from sim.provider import SimulatedSmsProvider, SmsProvider
from utils import metrics
from utils.logging import get_logger
from utils.redis import make_redis

log = get_logger("beacon.notify")

NOTIFY_QUEUE = "notify.outgoing"


def _ref(incident_id: str) -> str:
    return incident_id.split("-")[0].upper()


def _record(egress_channel: str, sent: int) -> None:
    metrics.notifications_total.labels(
        egress_channel=egress_channel,
        result="sent" if sent else "no_clients",
    ).inc()


async def notify_assignment(
    event: IncidentAssigned, *, hub: ConnectionHub, provider: SmsProvider
) -> None:
    payload = {
        "type": "incident.assigned",
        "incident_id": event.incident_id,
        "responder_id": event.responder_id,
        "responder_name": event.responder_name,
        "hospital_name": event.hospital_name,
        "severity": event.severity.value,
        "source_channel": event.source_channel.value,
        "match_radius_m": event.match_radius_m,
        "assigned_at": event.assigned_at.isoformat(),
    }

    # Dispatchers always watch on a data console.
    _record("websocket", await hub.broadcast(payload, role=ROLE_DISPATCHER))

    # The matched responder, on the channel they can actually use.
    if event.reachable_channel is ReachableChannel.SMS:
        message = (
            f"Beacon: assigned to a {event.severity.value} "
            f"{event.source_channel.value} incident (Ref {_ref(event.incident_id)})."
        )
        if event.hospital_name:
            message += f" Transport to {event.hospital_name}."
        try:
            await provider.send_sms(event.responder_contact, message)
            metrics.notifications_total.labels(
                egress_channel="sms", result="sent"
            ).inc()
        except Exception as exc:  # noqa: BLE001 — record the failure, don't crash
            log.warning("notify.sms_failed", error=str(exc))
            metrics.notifications_total.labels(
                egress_channel="sms", result="error"
            ).inc()
    else:
        _record("websocket", await hub.broadcast(payload, role=ROLE_RESPONDER))

    log.info(
        "notify.assignment",
        incident_id=event.incident_id,
        reachable_channel=event.reachable_channel.value,
    )


async def notify_status_change(
    event: IncidentStatusChanged, *, hub: ConnectionHub
) -> None:
    payload = {
        "type": "incident.status",
        "incident_id": event.incident_id,
        "status": event.status.value,
        "changed_at": event.changed_at.isoformat(),
    }
    _record("websocket", await hub.broadcast(payload, role=ROLE_DISPATCHER))
    log.info("notify.status", incident_id=event.incident_id, status=event.status.value)


def _make_handler(hub: ConnectionHub):
    async def handler(message: aio_pika.abc.AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            if message.routing_key == RK_ASSIGNED:
                event = IncidentAssigned.model_validate_json(message.body)
                redis = make_redis()
                try:
                    await notify_assignment(
                        event, hub=hub, provider=SimulatedSmsProvider(redis)
                    )
                finally:
                    await redis.aclose()
            else:  # RK_EN_ROUTE / RK_RESOLVED
                status_event = IncidentStatusChanged.model_validate_json(message.body)
                await notify_status_change(status_event, hub=hub)

    return handler


async def start_notification_consumer(
    bus: EventBus, hub: ConnectionHub
) -> aio_pika.abc.AbstractQueue:
    """Declare the notify queue and begin consuming assignment + status events."""
    queue = await bus.declare_queue(
        NOTIFY_QUEUE, [RK_ASSIGNED, RK_EN_ROUTE, RK_RESOLVED]
    )
    await queue.consume(_make_handler(hub))
    log.info("notify.consumer_started", queue=NOTIFY_QUEUE)
    return queue
