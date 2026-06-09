"""Beacon FastAPI application.

Boots the API and wires structlog + ``AuthMiddleware`` + every router (auth,
ingestion, incidents, realtime WS, simulator). The lifespan connects the RabbitMQ
bus and starts the background consumers (dispatch + notification), holding strong
references on ``app.state`` and closing the bus cleanly on shutdown. Boot is
resilient: if the broker is down the API still serves ``/health``, ``/metrics``,
and ingestion (incidents persist) — they just won't dispatch until it returns.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

from auth.middleware import AuthMiddleware
from auth.router import router as auth_router
from config import get_settings
from dispatch.engine import refresh_active_responders, start_dispatch_consumer
from events.bus import EventBus
from incidents.api import router as incidents_router
from ingestion.api import router as ingestion_router
from models.db import async_session
from notification.notifier import start_notification_consumer
from realtime.hub import hub
from realtime.hub import router as realtime_router
from sim.provider import router as sim_router
from utils import metrics
from utils.logging import configure_logging, get_logger

configure_logging()
log = get_logger("beacon")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("beacon.startup")
    settings = get_settings()
    app.state.bus = None
    # Connect the bus and start the dispatch consumer. Boot stays resilient: if
    # the broker is briefly unavailable the API still serves /health and ingestion
    # (incidents persist) — they just won't dispatch until the bus is back.
    try:
        bus = EventBus(
            settings.amqp_url, settings.event_exchange, settings.max_queue_priority
        )
        await bus.connect()
        app.state.bus = bus
        app.state.dispatch_queue = await start_dispatch_consumer(bus)
        app.state.notify_queue = await start_notification_consumer(bus, hub)
        log.info("beacon.bus_ready")
    except Exception as exc:  # noqa: BLE001 — degrade, don't crash the API
        log.error("beacon.bus_unavailable", error=str(exc))
    # Seed the active-responders gauge so the dashboard has data before the first
    # dispatch (best-effort: a DB hiccup must not block boot).
    try:
        async with async_session() as db:
            await refresh_active_responders(db)
    except Exception as exc:  # noqa: BLE001
        log.warning("beacon.gauge_init_failed", error=str(exc))
    yield
    bus = getattr(app.state, "bus", None)
    if bus is not None:
        await bus.close()
    log.info("beacon.shutdown")


app = FastAPI(title="Beacon", version="0.1.0", lifespan=lifespan)
app.add_middleware(AuthMiddleware)
app.include_router(auth_router)
app.include_router(ingestion_router)
app.include_router(incidents_router)
app.include_router(realtime_router)
app.include_router(sim_router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics", tags=["meta"])
async def metrics_endpoint() -> Response:
    payload, content_type = metrics.render_latest()
    return Response(content=payload, media_type=content_type)
