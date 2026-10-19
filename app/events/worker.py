"""Supervise broker setup and relay pending database events after outages."""

from __future__ import annotations

import asyncio

from config import get_settings
from events.bus import EventBus
from events.outbox import relay_one
from models.db import async_session
from utils.logging import get_logger

log = get_logger("beacon.worker")


async def run_event_worker(app, hub, stop: asyncio.Event) -> None:
    # Imports stay local to avoid coupling the domain consumers to their supervisor.
    from dispatch.engine import start_dispatch_consumer
    from notification.notifier import start_notification_consumer

    settings = get_settings()
    while not stop.is_set():
        bus = EventBus(
            settings.amqp_url, settings.event_exchange, settings.max_queue_priority
        )
        try:
            async with asyncio.timeout(settings.broker_timeout_seconds):
                await bus.connect()
                await start_dispatch_consumer(bus)
                await start_notification_consumer(bus, hub)
            app.state.bus = bus
            while not stop.is_set():
                try:
                    if bus.is_ready:
                        for _ in range(settings.outbox_batch_size):
                            async with async_session() as session:
                                if not await relay_one(session, bus):
                                    break
                except Exception as exc:
                    log.warning("worker.relay_pending", error_type=type(exc).__name__)
                try:
                    await asyncio.wait_for(stop.wait(), settings.outbox_poll_seconds)
                except TimeoutError:
                    pass
        except Exception as exc:
            log.warning("worker.setup_pending", error_type=type(exc).__name__)
        finally:
            app.state.bus = None
            try:
                async with asyncio.timeout(settings.broker_timeout_seconds):
                    await bus.close()
            except Exception as exc:
                log.warning("worker.close_failed", error_type=type(exc).__name__)
        if not stop.is_set():
            try:
                await asyncio.wait_for(
                    stop.wait(), settings.delivery_retry_base_seconds
                )
            except TimeoutError:
                pass
