"""Local provider simulator (stands in for Africa's Talking).

Two contracts make up a real USSD/SMS provider integration:

1. provider -> app (inbound): the gateway POSTs the user's USSD dial / inbound
   SMS to our webhooks. Those webhooks are the canonical ``/ingest/ussd`` and
   ``/ingest/sms`` endpoints (see ``ingestion/api.py``) — the HTML harness here
   drives them with exactly the payload a real gateway would send, so swapping
   in a real provider changes nothing on our side.
2. app -> provider (outbound): our app asks the gateway to deliver an SMS. That
   is the ``SmsProvider`` interface below. The simulated implementation records
   each message to an outbound SINK (a Redis list) instead of hitting a paid
   API, so tests and the demo can assert on "what would have been sent".

Keeping the simulator behind the SAME ``SmsProvider`` interface a real adapter
would implement makes production a drop-in swap. The sink lives in Redis (shared
across workers), never a module-level list.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path

import redis.asyncio as redis
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse

from utils.logging import get_logger
from utils.redis import get_redis

log = get_logger("beacon.sim")

OUTBOX_KEY = "sim:sms:outbox"
_OUTBOX_CAP = 200  # keep the demo sink bounded

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_HARNESS_FILE = _STATIC_DIR / "harness.html"


# --------------------------------------------------------------------------- #
# Outbound provider interface + simulated implementation
# --------------------------------------------------------------------------- #
class SmsProvider(ABC):
    """The outbound contract a real gateway adapter would also implement."""

    @abstractmethod
    async def send_sms(self, to: str, message: str) -> None:
        """Deliver an SMS to ``to``. Raises on transport failure."""
        raise NotImplementedError


class SimulatedSmsProvider(SmsProvider):
    """Records outbound SMS to a Redis list instead of a paid API."""

    def __init__(self, rc: redis.Redis) -> None:
        self._rc = rc

    async def send_sms(self, to: str, message: str) -> None:
        entry = json.dumps(
            {"to": to, "message": message, "ts": datetime.now(UTC).isoformat()}
        )
        # Newest first; trim so the sink can't grow without bound.
        await self._rc.lpush(OUTBOX_KEY, entry)
        await self._rc.ltrim(OUTBOX_KEY, 0, _OUTBOX_CAP - 1)
        log.info("sim.sms_out", to=to)


def get_provider(rc: redis.Redis = Depends(get_redis)) -> SimulatedSmsProvider:
    """FastAPI dependency: the active SMS provider (simulated locally)."""
    return SimulatedSmsProvider(rc)


async def read_outbox(rc: redis.Redis, limit: int = 50) -> list[dict]:
    """Return the most recent outbound SMS (newest first)."""
    raw = await rc.lrange(OUTBOX_KEY, 0, max(0, limit - 1))
    return [json.loads(item) for item in raw]


async def clear_outbox(rc: redis.Redis) -> int:
    """Empty the sink; return how many entries were removed."""
    count = await rc.llen(OUTBOX_KEY)
    await rc.delete(OUTBOX_KEY)
    return int(count)


# --------------------------------------------------------------------------- #
# Simulator console (demo harness + sink inspection)
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/sim", tags=["simulator"])


@router.get("/", include_in_schema=False)
async def harness() -> FileResponse:
    """Serve the static HTML harness used to 'dial' USSD and 'send' SMS."""
    return FileResponse(_HARNESS_FILE, media_type="text/html")


@router.get("/outbox")
async def outbox(limit: int = 50, rc: redis.Redis = Depends(get_redis)) -> JSONResponse:
    """List the outbound-SMS sink (what the gateway would have delivered)."""
    return JSONResponse({"messages": await read_outbox(rc, limit)})


@router.post("/outbox/clear")
async def outbox_clear(rc: redis.Redis = Depends(get_redis)) -> JSONResponse:
    return JSONResponse({"cleared": await clear_outbox(rc)})
