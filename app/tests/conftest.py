"""Tests require explicit service URLs and migrate a fresh random DB schema.

Database tests require BEACON_TEST_DATABASE_URL naming a beacon_test* database.
Endpoint fixtures roll back their changes. Concurrency tests use independent
connections and committed rows inside the isolated schema.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Override development settings before importing application modules.
os.environ.update(
    ENVIRONMENT="test",
    SECRET_KEY="isolated-beacon-test-signing-key-not-for-deployment",
    DATABASE_URL=os.getenv(
        "BEACON_TEST_DATABASE_URL",
        "postgresql+asyncpg://beacon:unused@127.0.0.1:1/beacon_test",
    ),
    REDIS_URL=os.getenv("BEACON_TEST_REDIS_URL", "redis://127.0.0.1:1/15"),
    AMQP_URL=os.getenv("BEACON_TEST_AMQP_URL", "amqp://unused:unused@127.0.0.1:1/"),
    INGEST_SHARED_SECRET="",
    DOCS_ENABLED="true",
    SIMULATOR_ENABLED="true",
    METRICS_PUBLIC="true",
)

from main import app  # noqa: E402
from models.db import get_session  # noqa: E402


@pytest.fixture(autouse=True)
def required_services(request):
    for marker, variable in (
        ("redis", "BEACON_TEST_REDIS_URL"),
        ("rabbitmq", "BEACON_TEST_AMQP_URL"),
    ):
        if request.node.get_closest_marker(marker) and not os.getenv(variable):
            pytest.skip(f"{variable} is required for this integration test")


@pytest.fixture
def test_engine():
    url = os.getenv("BEACON_TEST_DATABASE_URL")
    if not url:
        pytest.skip("BEACON_TEST_DATABASE_URL is required for PostgreSQL tests")
    if not (make_url(url).database or "").startswith("beacon_test"):
        pytest.fail("Refusing a database whose name does not start with beacon_test")
    schema = "beacon_test_" + uuid.uuid4().hex
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
    )

    async def prepare():
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))

            def migrate(sync_connection):
                config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
                config.set_main_option(
                    "script_location", str(Path(__file__).parents[1] / "alembic")
                )
                config.attributes["connection"] = sync_connection
                command.upgrade(config, "head")

            await conn.run_sync(migrate)

    async def cleanup():
        async with engine.begin() as conn:
            # Only the schema generated above is removed, never a configured name.
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()

    asyncio.run(prepare())
    try:
        yield engine
    finally:
        asyncio.run(cleanup())


@pytest.fixture
def session_factory(test_engine):
    return async_sessionmaker(test_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def app_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def db_session(test_engine):
    connection = await test_engine.connect()
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
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
