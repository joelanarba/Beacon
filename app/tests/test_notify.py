"""Notification fan-out + WebSocket hub.

The hub/notifier logic is exercised with a fake WebSocket (records what it would
send) and a fresh ``ConnectionHub`` per test, so it's deterministic and isolated
from the app's singleton. The real WS endpoint handshake/auth is covered with the
sync TestClient.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from auth.security import create_access_token
from channels.base import ChannelKind, Severity
from events.schemas import IncidentAssigned, IncidentStatusChanged
from main import app
from models.db import IncidentStatus, ReachableChannel
from notification.notifier import notify_assignment, notify_status_change
from realtime.hub import ROLE_DISPATCHER, ConnectionHub
from sim.provider import OUTBOX_KEY, SimulatedSmsProvider, read_outbox
from utils.redis import make_redis


class FakeWS:
    """Stand-in WebSocket that records the JSON it is asked to send."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def accept(self) -> None:
        pass

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


def _assigned(reachable: ReachableChannel, contact: str) -> IncidentAssigned:
    return IncidentAssigned(
        incident_id=str(uuid.uuid4()),
        responder_id=str(uuid.uuid4()),
        responder_name="Ambulance A1",
        responder_contact=contact,
        reachable_channel=reachable,
        source_channel=ChannelKind.USSD,
        severity=Severity.CRITICAL,
        match_radius_m=1234.5,
    )


# --------------------------------------------------------------------------- #
# Hub
# --------------------------------------------------------------------------- #
async def test_hub_disconnect_removes_connection():
    hub = ConnectionHub()
    ws = FakeWS()
    await hub.connect(ws, ROLE_DISPATCHER)
    await hub.disconnect(ws, ROLE_DISPATCHER)
    assert await hub.broadcast({"type": "x"}, role=ROLE_DISPATCHER) == 0


# --------------------------------------------------------------------------- #
# Fan-out
# --------------------------------------------------------------------------- #
async def test_dispatcher_receives_assignment_over_websocket():
    hub = ConnectionHub()
    ws = FakeWS()
    await hub.connect(ws, ROLE_DISPATCHER)
    rc = make_redis()
    try:
        await notify_assignment(
            _assigned(ReachableChannel.APP, "+233200000111"),
            hub=hub,
            provider=SimulatedSmsProvider(rc),
        )
    finally:
        await rc.aclose()
    assert len(ws.sent) == 1
    assert ws.sent[0]["type"] == "incident.assigned"
    assert ws.sent[0]["responder_name"] == "Ambulance A1"


async def test_sms_responder_gets_outbox_message():
    hub = ConnectionHub()
    rc = make_redis()
    await rc.delete(OUTBOX_KEY)
    event = _assigned(ReachableChannel.SMS, "+233200000888")
    await notify_assignment(event, hub=hub, provider=SimulatedSmsProvider(rc))
    messages = await read_outbox(rc, 10)
    await rc.aclose()
    assert any(m["to"] == "+233200000888" for m in messages)


async def test_status_change_propagates_over_websocket():
    hub = ConnectionHub()
    ws = FakeWS()
    await hub.connect(ws, ROLE_DISPATCHER)
    event = IncidentStatusChanged(
        incident_id=str(uuid.uuid4()), status=IncidentStatus.EN_ROUTE
    )
    await notify_status_change(event, hub=hub)
    assert ws.sent[0]["type"] == "incident.status"
    assert ws.sent[0]["status"] == "EN_ROUTE"


# --------------------------------------------------------------------------- #
# WebSocket endpoint (handshake + auth) — sync TestClient
# --------------------------------------------------------------------------- #
def test_ws_dispatch_accepts_valid_token():
    token = create_access_token("user-1", extra={"role": "DISPATCHER"})
    client = TestClient(app)
    with client.websocket_connect(f"/ws/dispatch?token={token}") as ws:
        assert ws is not None  # handshake accepted


def test_ws_dispatch_rejects_missing_token():
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/dispatch"):
            pass
