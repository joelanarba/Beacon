"""Ingestion API — the convergence point of the three channels.

``/ingest/app``, ``/ingest/ussd`` and ``/ingest/sms`` are the provider-facing
webhooks. Each drives its channel adapter, and all three converge to ONE
``IncidentEvent`` which is persisted as a ``REPORTED`` ``Incident``. Capability
differences live on the event (fields present/absent), never as a different
downstream path, so the dispatch engine consumes the same event shape regardless
of origin.

USSD/SMS endpoints speak the real provider contract:
- USSD takes ``sessionId / phoneNumber / text`` (form-encoded, ``text``
  accumulating menu choices) and replies ``text/plain`` prefixed ``CON ``
  (continue) or ``END `` (terminate).
- SMS takes ``from / text`` and replies with the text to send back.

Complete reports and their ``incident.reported`` outbox records commit together.
The background relay publishes them independently of the request.
"""

from __future__ import annotations

import hmac

import redis.asyncio as redis
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from channels.app_channel import AppChannel
from channels.base import IncidentEvent
from channels.sms_channel import SMSChannel
from channels.ussd_channel import END, USSDChannel
from config import get_settings
from incidents.acceptance import IdempotencyConflict, accept_incident, find_accepted
from models.db import IncidentStatus, get_session
from models.schemas import IngestAck, IngestAppRequest
from utils import metrics
from utils.logging import get_logger
from utils.redis import get_redis

log = get_logger("beacon.ingestion")


async def require_ingest_secret(
    x_beacon_ingest_secret: str | None = Header(default=None),
) -> None:
    expected = get_settings().ingest_shared_secret
    if expected and not hmac.compare_digest(x_beacon_ingest_secret or "", expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid ingest secret"
        )


router = APIRouter(
    prefix="/ingest", tags=["ingestion"], dependencies=[Depends(require_ingest_secret)]
)

# Channel adapters are stateless (per-session state lives in Redis, not here).
_app = AppChannel()
_ussd = USSDChannel()
_sms = SMSChannel()

_USSD_KEY = "ussd:session:{session_id}"


def _ref(incident_id: str) -> str:
    """A short, human-readable reference for the reporter."""
    return incident_id.split("-")[0].upper()


def _validate_identity(identity: str | None) -> str | None:
    if identity is not None and (not identity.strip() or len(identity) > 128):
        raise HTTPException(
            422, detail="Report identity must contain 1 to 128 characters"
        )
    return identity


async def _persist_incident(
    session: AsyncSession, event: IncidentEvent, identity: str | None = None
) -> None:
    try:
        incident = await accept_incident(session, event, identity)
    except IdempotencyConflict as exc:
        raise HTTPException(409, detail=str(exc)) from None
    event.incident_id = incident.id


# --------------------------------------------------------------------------- #
# App (rich data channel)
# --------------------------------------------------------------------------- #
@router.post("/app", response_model=IngestAck, status_code=status.HTTP_201_CREATED)
async def ingest_app(
    request: Request,
    body: IngestAppRequest,
    idempotency_key: str | None = Header(default=None, min_length=1, max_length=128),
    session: AsyncSession = Depends(get_session),
) -> IngestAck:
    event = await _app.parse(body.model_dump())
    await _persist_incident(session, event, _validate_identity(idempotency_key))
    log.info(
        "app.reported",
        incident_id=event.incident_id,
        emergency_type=event.emergency_type.value,
        severity=event.severity.value,
    )
    return IngestAck(
        incident_id=event.incident_id,
        status=IncidentStatus.REPORTED,
        source_channel=event.source_channel,
        severity=event.severity,
    )


# --------------------------------------------------------------------------- #
# USSD (session, menu-driven, GSM signaling)
# --------------------------------------------------------------------------- #
async def _ussd_session_alive(
    rc: redis.Redis, key: str, first_dial: bool, ttl: int
) -> bool:
    """Track session liveness in Redis (TTL = configured session window).

    Fail-open: if Redis is unavailable we keep the menu walk working (the
    accumulated ``text`` already carries the menu position) and simply lose
    timeout detection. Redis is fail-open on non-critical paths.
    """
    try:
        if first_dial:
            await rc.set(key, "1", ex=ttl)
            return True
        # A continuation refreshes the window iff the session still exists.
        return bool(await rc.expire(key, ttl))
    except Exception as exc:  # noqa: BLE001 — fail-open is intentional
        log.warning("ussd.redis_unavailable", error=str(exc))
        return True


async def _ussd_clear(rc: redis.Redis, key: str) -> None:
    try:
        await rc.delete(key)
    except Exception as exc:  # noqa: BLE001 — non-critical cleanup
        log.warning("ussd.redis_cleanup_failed", error=str(exc))


@router.post("/ussd")
async def ingest_ussd(
    request: Request,
    session: AsyncSession = Depends(get_session),
    rc: redis.Redis = Depends(get_redis),
) -> PlainTextResponse:
    form = await request.form()
    raw = {
        "sessionId": str(form.get("sessionId", "") or ""),
        "phoneNumber": str(form.get("phoneNumber", "") or ""),
        "text": str(form.get("text", "") or ""),
    }
    session_id = raw["sessionId"]
    if not session_id or not raw["phoneNumber"] or len(raw["phoneNumber"]) > 64:
        raise HTTPException(422, detail="A session ID and reporter phone are required")
    _validate_identity(session_id)
    result = await _ussd.parse(raw)
    if isinstance(result, IncidentEvent):
        try:
            previous = await find_accepted(session, result, session_id)
        except IdempotencyConflict as exc:
            raise HTTPException(409, detail=str(exc)) from None
        if previous is not None:
            return PlainTextResponse(END + f"Report received. Ref: {_ref(previous.id)}")
    first_dial = raw["text"] == ""
    ttl = get_settings().ussd_session_ttl_seconds
    key = _USSD_KEY.format(session_id=session_id)

    alive = await _ussd_session_alive(rc, key, first_dial, ttl)
    if not first_dial and not alive:
        metrics.ussd_sessions_total.labels(outcome="timeout").inc()
        log.info("ussd.timeout", session_id=session_id)
        return PlainTextResponse(
            END + "Your session expired. Please dial again to report."
        )

    if isinstance(result, IncidentEvent):
        await _persist_incident(session, result, session_id)
        metrics.ussd_sessions_total.labels(outcome="completed").inc()
        await _ussd_clear(rc, key)
        log.info(
            "ussd.completed",
            session_id=session_id,
            incident_id=result.incident_id,
            emergency_type=result.emergency_type.value,
        )
        return PlainTextResponse(
            END + f"Report received. Ref: {_ref(result.incident_id)}"
        )

    # PartialSession: either keep walking (CON) or a terminal non-completion.
    if result.done:
        metrics.ussd_sessions_total.labels(outcome="abandoned").inc()
        await _ussd_clear(rc, key)
        log.info("ussd.abandoned", session_id=session_id)
    return PlainTextResponse(result.reply)


# --------------------------------------------------------------------------- #
# SMS (async, store-and-forward, GSM signaling)
# --------------------------------------------------------------------------- #
@router.post("/sms")
async def ingest_sms(
    request: Request,
    idempotency_key: str | None = Header(default=None, min_length=1, max_length=128),
    session: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    form = await request.form()
    raw = {
        "from": str(form.get("from", "") or ""),
        "text": str(form.get("text", "") or ""),
    }
    result = await _sms.parse(raw)

    if isinstance(result, IncidentEvent):
        provider_id = form.get("id") or form.get("messageId")
        identity = (
            idempotency_key
            if idempotency_key is not None
            else (str(provider_id) if provider_id is not None else None)
        )
        await _persist_incident(session, result, _validate_identity(identity))
        log.info(
            "sms.reported",
            incident_id=result.incident_id,
            emergency_type=result.emergency_type.value,
        )
        return PlainTextResponse(
            f"Beacon: {result.emergency_type.value} report received. "
            f"Ref: {_ref(result.incident_id)}. Help is being arranged."
        )

    # Unparseable — reply with the keyword format, persist nothing.
    log.info("sms.unparseable", sender=raw["from"])
    return PlainTextResponse(result.reply)
