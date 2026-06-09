"""WebSocket hub + the dispatcher real-time endpoint.

The hub is an in-process registry of live WebSocket connections keyed by role.
This is the one place a module-level structure is correct rather than Redis: a
WebSocket is a live socket handle bound to *this* worker — it cannot be
serialised or shared across processes. (For multi-worker broadcast you'd bridge
workers with Redis pub/sub; single-worker here, so the registry is local. That's
future work, noted in the README.)

Dispatchers connect to ``/ws/dispatch`` with an access token and receive every
assignment + status transition live. The HTTP ``AuthMiddleware`` only guards the
``http`` scope, so the token is validated here in the WebSocket handler instead.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError

from auth.security import decode_token
from utils.logging import get_logger

log = get_logger("beacon.realtime")

ROLE_DISPATCHER = "dispatcher"
ROLE_RESPONDER = "responder"


class ConnectionHub:
    """Role-keyed set of live WebSocket connections with safe broadcast."""

    def __init__(self) -> None:
        self._by_role: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, role: str) -> None:
        await websocket.accept()
        async with self._lock:
            self._by_role[role].add(websocket)
        log.info("ws.connected", role=role, total=len(self._by_role[role]))

    async def disconnect(self, websocket: WebSocket, role: str) -> None:
        async with self._lock:
            self._by_role.get(role, set()).discard(websocket)
        log.info("ws.disconnected", role=role)

    async def broadcast(self, message: dict, *, role: str | None = None) -> int:
        """Send ``message`` (as JSON) to connections; return how many got it.

        Dead sockets (send raised) are pruned. ``role=None`` targets everyone.
        """
        async with self._lock:
            if role is None:
                targets = {ws for conns in self._by_role.values() for ws in conns}
            else:
                targets = set(self._by_role.get(role, set()))

        sent = 0
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(message)
                sent += 1
            except Exception:  # noqa: BLE001 — a broken socket must not stop fan-out
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    for conns in self._by_role.values():
                        conns.discard(ws)
        return sent


# Process-wide singleton used by the app (tests construct their own instances).
hub = ConnectionHub()

router = APIRouter(tags=["realtime"])


@router.websocket("/ws/dispatch")
async def ws_dispatch(websocket: WebSocket, token: str = "") -> None:
    """Dispatcher live feed. Authenticate via ``?token=<access token>``."""
    try:
        claims = decode_token(token)
        if claims.get("type") != "access":
            raise JWTError("not an access token")
    except JWTError:
        await websocket.close(code=4401)  # policy violation: bad/missing token
        return

    await hub.connect(websocket, ROLE_DISPATCHER)
    try:
        while True:
            # We don't expect inbound messages; receiving keeps the socket open
            # and surfaces disconnects promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        # Prune on any exit path (disconnect or unexpected error), so the hub
        # never broadcasts to a dead socket.
        await hub.disconnect(websocket, ROLE_DISPATCHER)
