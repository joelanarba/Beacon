"""Beacon API with a supervised broker/outbox worker.

Ingestion requires PostgreSQL, but does not wait for RabbitMQ. Accepted reports
and their dispatch events commit together. /health is liveness; /ready checks
broker setup, the outbox schema, and Redis connectivity.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from auth.middleware import AuthMiddleware
from auth.router import router as auth_router
from config import get_settings
from dispatch.engine import refresh_active_responders
from events.worker import run_event_worker
from incidents.api import router as incidents_router
from ingestion.api import router as ingestion_router
from models.db import EventOutbox, async_session, engine
from sqlalchemy import select
from realtime.hub import hub
from realtime.hub import router as realtime_router
from sim.provider import router as sim_router
from utils import metrics
from utils.logging import configure_logging, get_logger
from utils.redis import make_redis

configure_logging()
log = get_logger("beacon")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("beacon.startup")
    app.state.bus = None
    stop = asyncio.Event()
    worker = asyncio.create_task(run_event_worker(app, hub, stop))
    app.state.event_worker = worker
    # Seed the active-responders gauge so the dashboard has data before the first
    # dispatch (best-effort: a DB hiccup must not block boot).
    try:
        async with asyncio.timeout(settings.broker_timeout_seconds):
            async with async_session() as db:
                await refresh_active_responders(db)
    except Exception as exc:  # noqa: BLE001
        log.warning("beacon.gauge_init_failed", error=str(exc))
    try:
        yield
    finally:
        stop.set()
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
        await engine.dispose()
    log.info("beacon.shutdown")


app = FastAPI(
    title="Beacon",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.add_middleware(AuthMiddleware)
app.include_router(auth_router)
app.include_router(ingestion_router)
app.include_router(incidents_router)
app.include_router(realtime_router)
if settings.simulator_enabled:
    app.include_router(sim_router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", tags=["meta"])
async def ready(response: Response) -> dict[str, str]:
    bus = getattr(app.state, "bus", None)
    if bus is not None and bus.is_ready:
        try:
            async with asyncio.timeout(get_settings().broker_timeout_seconds):
                async with async_session() as session:
                    await session.execute(select(EventOutbox.id).limit(1))
                rc = make_redis()
                try:
                    await rc.ping()
                finally:
                    await rc.aclose()
            return {"status": "ready"}
        except Exception:
            pass
    response.status_code = 503
    return {"status": "not_ready"}


@app.get("/metrics", tags=["meta"])
async def metrics_endpoint() -> Response:
    payload, content_type = metrics.render_latest()
    return Response(content=payload, media_type=content_type)
