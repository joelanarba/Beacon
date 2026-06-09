"""RabbitMQ event bus (aio-pika).

One durable **topic** exchange carries the whole incident lifecycle. Queues are
declared durable with ``x-max-priority`` so they are genuine **priority queues**:
a CRITICAL message jumps ahead of a STANDARD one already waiting — which is the
entire reason RabbitMQ was chosen over Kafka for emergency dispatch. Messages are
published **persistent** with a priority derived from triage severity, and
consumers run with a bounded prefetch.

The bus object is created once in the app lifespan (``main.py``) and held on
``app.state``; publishing from the request path is fire-and-forget.
"""

from __future__ import annotations

import aio_pika
from pydantic import BaseModel

from utils.logging import get_logger

log = get_logger("beacon.bus")


class EventBus:
    def __init__(
        self,
        amqp_url: str,
        exchange_name: str,
        max_priority: int,
        prefetch: int = 16,
    ) -> None:
        self._amqp_url = amqp_url
        self._exchange_name = exchange_name
        self._max_priority = max_priority
        self._prefetch = prefetch
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractRobustChannel | None = None
        self._exchange: aio_pika.abc.AbstractExchange | None = None

    @property
    def max_priority(self) -> int:
        return self._max_priority

    async def connect(self) -> None:
        self._connection = await aio_pika.connect_robust(self._amqp_url)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=self._prefetch)
        self._exchange = await self._channel.declare_exchange(
            self._exchange_name, aio_pika.ExchangeType.TOPIC, durable=True
        )
        log.info("bus.connected", exchange=self._exchange_name)

    async def declare_queue(
        self,
        name: str,
        routing_keys: list[str],
        *,
        durable: bool = True,
        auto_delete: bool = False,
    ) -> aio_pika.abc.AbstractQueue:
        """Declare a priority queue and bind it to the given routing keys."""
        assert self._channel is not None and self._exchange is not None
        queue = await self._channel.declare_queue(
            name,
            durable=durable,
            auto_delete=auto_delete,
            arguments={"x-max-priority": self._max_priority},
        )
        for routing_key in routing_keys:
            await queue.bind(self._exchange, routing_key)
        return queue

    async def publish(
        self, routing_key: str, payload: BaseModel, priority: int = 0
    ) -> None:
        """Publish a persistent, priority-tagged message (fire-and-forget)."""
        assert self._exchange is not None
        message = aio_pika.Message(
            body=payload.model_dump_json().encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            priority=priority,
        )
        await self._exchange.publish(message, routing_key=routing_key)

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            log.info("bus.closed")
