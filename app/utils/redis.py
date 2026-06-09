"""Async Redis client helper.

Redis holds Beacon's shared state: USSD session liveness and responder GEO
positions. That state has to be shared across workers, so it lives in Redis
rather than a module-level dict.

A fresh client is created per request and closed afterwards. This keeps the
connection bound to the active event loop, which matters under pytest where each
test runs in its own loop (the same reasoning behind NullPool for the DB engine).
``decode_responses=True`` so callers get ``str`` back, not ``bytes``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import redis.asyncio as redis

from config import get_settings


def make_redis() -> redis.Redis:
    """Construct a new async Redis client from settings."""
    return redis.from_url(get_settings().redis_url, decode_responses=True)


async def get_redis() -> AsyncIterator[redis.Redis]:
    """FastAPI dependency: one Redis client per request, closed on exit."""
    client = make_redis()
    try:
        yield client
    finally:
        await client.aclose()
