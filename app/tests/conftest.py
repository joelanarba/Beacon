"""Pytest fixtures.

- ``app_client``  — an ASGI httpx client for endpoints that need no DB
  (``/health``, ``/metrics``).
- ``db_session``  — an AsyncSession wrapped in an outer transaction that is
  rolled back after each test, so no data persists. Uses
  ``join_transaction_mode="create_savepoint"`` so handler-side ``commit()``
  calls map onto savepoints rather than the real transaction.
- ``client``      — an ASGI client with ``get_session`` overridden to the
  transactional ``db_session``.

The engine uses NullPool under pytest (see ``models/db.py``), so per-test
connections never leak across event loops. The schema is created lazily on
first DB use (idempotent), so non-DB tests don't require a database.
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from main import app
from models.db import Base, engine, get_session

_schema_ready = False


async def _ensure_schema() -> None:
    global _schema_ready
    if not _schema_ready:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _schema_ready = True


@pytest_asyncio.fixture
async def app_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def db_session():
    await _ensure_schema()
    connection = await engine.connect()
    trans = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()
        if trans.is_active:
            await trans.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
