"""Mounted only by the study; preserve production transactions and delivery logic."""

from __future__ import annotations

import asyncio
import math
import os
import sys
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime


@contextmanager
def policy_hooks(policy: str, delay_ms: float):
    if policy not in {"equal", "severity"}:
        raise ValueError("Unknown study policy")
    if not math.isfinite(delay_ms) or not 0 <= delay_ms <= 200:
        raise ValueError("Invalid study dispatch delay")
    from dispatch import engine
    from events import outbox
    from events.bus import EventBus
    from incidents import acceptance, api

    enqueue, publish = outbox.enqueue_event, EventBus.publish
    initialize, consume = EventBus.__init__, outbox.consume_reliably
    dispatch = engine.dispatch_incident
    state = {"policy": policy, "delay_ms": delay_ms, "prefetch": None, "deliveries": []}

    async def enqueue_with_policy(*args, **kwargs):
        if policy == "equal":
            kwargs["priority"] = 0
        return await enqueue(*args, **kwargs)

    async def publish_with_policy(self, routing_key, payload, priority=0, **kwargs):
        return await publish(
            self, routing_key, payload, 0 if policy == "equal" else priority, **kwargs
        )

    def initialize_with_prefetch(
        self, amqp_url, exchange_name, max_priority, prefetch=16
    ):
        initialize(self, amqp_url, exchange_name, max_priority, prefetch=1)
        state["prefetch"] = self._prefetch

    async def consume_with_observation(message, consumer, process):
        state["deliveries"].append(
            {
                "event_id": message.message_id,
                "routing_key": message.routing_key,
                "priority": message.priority,
                "at": datetime.now(UTC).isoformat(),
            }
        )
        return await consume(message, consumer, process)

    async def dispatch_with_delay(*args, **kwargs):
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000)
        return await dispatch(*args, **kwargs)

    patches = [
        (module, "enqueue_event", enqueue_with_policy)
        for module in (outbox, engine, acceptance, api)
    ] + [
        (EventBus, "publish", publish_with_policy),
        (EventBus, "__init__", initialize_with_prefetch),
        (outbox, "consume_reliably", consume_with_observation),
        (engine, "consume_reliably", consume_with_observation),
        (engine, "dispatch_incident", dispatch_with_delay),
    ]
    # Notification imports may already exist in local tests, too.
    from notification import notifier

    patches.append((notifier, "consume_reliably", consume_with_observation))
    originals = [(target, name, getattr(target, name)) for target, name, _ in patches]
    try:
        for target, name, replacement in patches:
            setattr(target, name, replacement)
        yield state
    finally:
        for target, name, original in reversed(originals):
            setattr(target, name, original)


async def main():
    if os.environ.get("ENVIRONMENT") != "test":
        raise RuntimeError("Study launcher requires its isolated test environment")
    import uvicorn
    from fastapi import FastAPI

    sys.path.insert(0, "/app")
    with policy_hooks(
        os.environ["BEACON_STUDY_POLICY"], float(os.environ["BEACON_STUDY_DELAY_MS"])
    ) as state:
        from main import app

        control = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @control.get("/study")
        async def study():
            return state

        api = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8000))
        probe = uvicorn.Server(
            uvicorn.Config(control, host="0.0.0.0", port=9010, access_log=False)
        )
        probe.capture_signals = nullcontext
        task = asyncio.create_task(probe.serve())
        try:
            await api.serve()
        finally:
            probe.should_exit = True
            await task


if __name__ == "__main__":
    asyncio.run(main())
