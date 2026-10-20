"""Durable priority queues and confirmed publication for the outbox relay.

Priority applies to messages still waiting in the broker, subject to prefetch.
The supervised worker owns the connection and restores initial setup after failure.
"""

from __future__ import annotations

import asyncio
import json

import aio_pika
from pydantic import BaseModel

from config import get_settings
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
        # aio-pika 9.4.1 tracks robust queues weakly. Keep their bindings and
        # consumer callbacks alive so they can be restored after reconnecting.
        self._queues: list[aio_pika.abc.AbstractQueue] = []

    @property
    def max_priority(self) -> int:
        return self._max_priority

    @property
    def is_ready(self) -> bool:
        return bool(
            self._connection is not None
            and self._connection.connected.is_set()
            and self._channel is not None
            and not self._channel.is_closed
        )

    async def connect(self) -> None:
        self._connection = await aio_pika.connect_robust(
            self._amqp_url, timeout=get_settings().broker_timeout_seconds
        )
        self._channel = await self._connection.channel(
            publisher_confirms=True, on_return_raises=True
        )
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
        self._queues.append(queue)
        for routing_key in routing_keys:
            await queue.bind(self._exchange, routing_key)
        return queue

    async def publish(
        self,
        routing_key: str,
        payload: BaseModel | dict,
        priority: int = 0,
        *,
        message_id: str | None = None,
        headers: dict | None = None,
    ) -> None:
        """Await broker confirmation; reject unroutable messages and bound the wait."""
        assert self._exchange is not None
        message = aio_pika.Message(
            body=(
                payload.model_dump_json()
                if isinstance(payload, BaseModel)
                else json.dumps(payload)
            ).encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            priority=priority,
            message_id=message_id,
            headers=headers,
        )
        async with asyncio.timeout(get_settings().broker_timeout_seconds):
            await self._exchange.publish(
                message,
                routing_key=routing_key,
                mandatory=True,
                timeout=get_settings().broker_timeout_seconds,
            )

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            log.info("bus.closed")
        self._queues.clear()
