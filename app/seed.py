"""Seed a few responders, hospitals, and one dispatcher user for local testing.

Idempotent (skip-if-exists), so it is safe to re-run. Run inside the app
container after migrating:

    docker compose exec app alembic upgrade head
    docker compose exec app python seed.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from auth.security import hash_password
from config import get_settings
from models.db import (
    Hospital,
    ReachableChannel,
    Responder,
    ResponderStatus,
    ResponderType,
    User,
    UserRole,
    async_session,
)
from utils.logging import configure_logging, get_logger

configure_logging()
log = get_logger("beacon.seed")

# name, type, status, contact, reachable_channel, longitude, latitude
_RESPONDERS = [
    (
        "Ambulance A1",
        ResponderType.AMBULANCE,
        ResponderStatus.AVAILABLE,
        "+233200000001",
        ReachableChannel.APP,
        -0.1870,
        5.6037,
    ),
    (
        "Ambulance A2",
        ResponderType.AMBULANCE,
        ResponderStatus.AVAILABLE,
        "+233200000002",
        ReachableChannel.SMS,
        -0.2010,
        5.5600,
    ),
    (
        "Fire Unit F1",
        ResponderType.FIRE,
        ResponderStatus.AVAILABLE,
        "+233200000003",
        ReachableChannel.APP,
        -0.1700,
        5.6100,
    ),
    (
        "Police P1",
        ResponderType.POLICE,
        ResponderStatus.AVAILABLE,
        "+233200000004",
        ReachableChannel.SMS,
        -0.2200,
        5.5500,
    ),
    (
        "Volunteer V1",
        ResponderType.VOLUNTEER,
        ResponderStatus.OFFLINE,
        "+233200000005",
        ReachableChannel.SMS,
        -0.1900,
        5.5900,
    ),
]

# name, latitude, longitude, total_capacity, available_capacity, specialties
_HOSPITALS = [
    (
        "Korle Bu Teaching Hospital",
        5.5366,
        -0.2261,
        200,
        35,
        ["TRAUMA", "CARDIOLOGY", "GENERAL"],
    ),
    ("37 Military Hospital", 5.5836, -0.1854, 120, 20, ["TRAUMA", "SURGERY"]),
    ("Ridge Hospital", 5.5650, -0.1969, 80, 12, ["GENERAL", "MATERNITY"]),
]

_DISPATCHER_EMAIL = "dispatcher@beacon.local"
_DISPATCHER_PASSWORD = "beacon-dispatch"


async def _seed_responders(session) -> list[tuple[str, float, float]]:
    positions: list[tuple[str, float, float]] = []
    for name, rtype, rstatus, contact, channel, lng, lat in _RESPONDERS:
        result = await session.execute(select(Responder).where(Responder.name == name))
        existing = result.scalar_one_or_none()
        if existing is None:
            responder = Responder(
                name=name,
                type=rtype,
                status=rstatus,
                contact=contact,
                reachable_channel=channel,
            )
            session.add(responder)
            await session.flush()
            positions.append((responder.id, lng, lat))
            log.info("seeded.responder", name=name)
        else:
            positions.append((existing.id, lng, lat))
    return positions


async def _seed_hospitals(session) -> None:
    for name, lat, lng, total, available, specialties in _HOSPITALS:
        result = await session.execute(select(Hospital).where(Hospital.name == name))
        if result.scalar_one_or_none() is None:
            session.add(
                Hospital(
                    name=name,
                    latitude=lat,
                    longitude=lng,
                    total_capacity=total,
                    available_capacity=available,
                    specialties=list(specialties),
                )
            )
            log.info("seeded.hospital", name=name)


async def _seed_dispatcher(session) -> None:
    result = await session.execute(select(User).where(User.email == _DISPATCHER_EMAIL))
    if result.scalar_one_or_none() is None:
        session.add(
            User(
                email=_DISPATCHER_EMAIL,
                full_name="Demo Dispatcher",
                hashed_password=hash_password(_DISPATCHER_PASSWORD),
                role=UserRole.DISPATCHER,
            )
        )
        log.info("seeded.user", email=_DISPATCHER_EMAIL)


async def _seed_geo(positions: list[tuple[str, float, float]]) -> None:
    """Push responder live positions to Redis GEO (fail-open if Redis is down)."""
    if not positions:
        return
    try:
        import redis.asyncio as redis

        client = redis.from_url(get_settings().redis_url)
        for responder_id, lng, lat in positions:
            await client.geoadd("responders:live", [lng, lat, responder_id])
        await client.aclose()
        log.info("seeded.geo", count=len(positions))
    except Exception as exc:
        log.warning("seed.geo_skipped", error=str(exc))


async def seed() -> None:
    async with async_session() as session:
        positions = await _seed_responders(session)
        await _seed_hospitals(session)
        await _seed_dispatcher(session)
        await session.commit()
    await _seed_geo(positions)
    log.info("seed.done")


if __name__ == "__main__":
    asyncio.run(seed())
