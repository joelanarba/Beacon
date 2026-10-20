"""Test-only entry point mounted by the integration Compose file.

Barriers hold real publication/acknowledgement boundaries until the host test
injects a fault. This file is never copied into the production application image.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import nullcontext
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class ArmRequest(BaseModel):
    stage: str
    routing_key: str = "incident.reported"


@dataclass
class Barrier:
    stage: str = ""
    routing_key: str = ""
    reached: bool = False
    event_id: str | None = None
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def hit(self, stage: str, routing_key: str, event_id: str | None):
        if stage == self.stage and routing_key == self.routing_key:
            self.reached = True
            self.event_id = event_id
            # Fail the test if the controller vanishes; do not strand a worker.
            await asyncio.wait_for(self.release.wait(), 120)


def make_control(barrier: Barrier) -> FastAPI:
    control = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @control.post("/arm")
    async def arm(body: ArmRequest):
        if body.stage not in {
            "before_process",
            "after_publish",
            "before_ack",
            "before_failure_store",
        }:
            raise HTTPException(422, "Unknown barrier")
        if barrier.stage and not barrier.release.is_set():
            raise HTTPException(409, "Release the previous barrier first")
        barrier.stage, barrier.routing_key = body.stage, body.routing_key
        barrier.reached, barrier.event_id = False, None
        barrier.release = asyncio.Event()
        return {"armed": body.stage}

    @control.get("/state")
    async def state():
        return {
            "stage": barrier.stage,
            "reached": barrier.reached,
            "event_id": barrier.event_id,
        }

    @control.post("/release")
    async def release():
        barrier.stage = ""
        barrier.release.set()
        return {"released": True}

    return control


def install_barriers(barrier: Barrier):
    from aio_pika.message import IncomingMessage

    from events import outbox
    from events.bus import EventBus

    publish, ack, failure = EventBus.publish, IncomingMessage.ack, outbox.record_failure
    consume = outbox.consume_reliably

    async def consume_with_barrier(message, consumer, process):
        await barrier.hit("before_process", message.routing_key, message.message_id)
        return await consume(message, consumer, process)

    async def publish_with_barrier(self, routing_key, payload, *args, **kwargs):
        await publish(self, routing_key, payload, *args, **kwargs)
        await barrier.hit("after_publish", routing_key, kwargs.get("message_id"))

    async def ack_with_barrier(self, *args, **kwargs):
        await barrier.hit("before_ack", self.routing_key, self.message_id)
        return await ack(self, *args, **kwargs)

    async def failure_with_barrier(session, message, *args, **kwargs):
        await barrier.hit(
            "before_failure_store", message.routing_key, message.message_id
        )
        return await failure(session, message, *args, **kwargs)

    EventBus.publish = publish_with_barrier
    IncomingMessage.ack = ack_with_barrier
    outbox.record_failure = failure_with_barrier
    outbox.consume_reliably = consume_with_barrier


async def main():
    import uvicorn

    # The script lives under /opt; application imports still come from /app.
    sys.path.insert(0, "/app")
    barrier = Barrier()
    install_barriers(barrier)
    from main import app

    api = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8000))
    control = uvicorn.Server(
        uvicorn.Config(
            make_control(barrier), host="0.0.0.0", port=9010, access_log=False
        )
    )
    # One signal owner. API lifespan remains the production application's own.
    control.capture_signals = nullcontext
    task = asyncio.create_task(control.serve())
    try:
        await api.serve()
    finally:
        barrier.release.set()
        control.should_exit = True
        await task


if __name__ == "__main__":
    asyncio.run(main())
