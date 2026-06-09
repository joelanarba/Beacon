"""Responder matching via Redis GEO.

Live responder positions are ephemeral and high-churn, so they live in a Redis
GEO set (``responders:live``), not Postgres. Matching is a two-step join:

1. ``GEOSEARCH`` the GEO set in an expanding radius → candidate ids, nearest
   first. Redis does the distance ranking for us.
2. Filter those candidates against Postgres for the required responder type and
   ``AVAILABLE`` status (type/status are authoritative in the DB, not Redis).

The first candidate that survives the filter — i.e. the nearest available
responder of the right discipline — wins. If a radius yields no survivor we widen
and retry, up to the configured ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from channels.base import EmergencyType
from dispatch.triage import required_responder_type
from models.db import Responder, ResponderStatus
from utils.logging import get_logger

log = get_logger("beacon.match.responder")

RESPONDERS_GEO_KEY = "responders:live"


@dataclass
class ResponderMatch:
    responder: Responder
    distance_m: float


def _member_and_distance(row: object) -> tuple[str, float]:
    """Normalise a redis-py GEOSEARCH ``withdist`` row into ``(id, metres)``."""
    # With ``withdist=True`` each row is ``[member, distance]``.
    member, distance = row[0], row[1]  # type: ignore[index]
    return str(member), float(distance)


async def match_nearest(
    db: AsyncSession,
    redis,
    *,
    emergency_type: EmergencyType,
    latitude: float,
    longitude: float,
    start_radius_m: int,
    max_radius_m: int,
    geo_key: str = RESPONDERS_GEO_KEY,
) -> ResponderMatch | None:
    """Nearest AVAILABLE responder of the required type, or None."""
    required = required_responder_type(emergency_type)
    radius = start_radius_m
    while radius <= max_radius_m:
        try:
            rows = await redis.geosearch(
                geo_key,
                longitude=longitude,
                latitude=latitude,
                radius=radius,
                unit="m",
                sort="ASC",
                withdist=True,
            )
        except Exception as exc:  # noqa: BLE001 — Redis down is non-fatal here
            log.warning("match.geo_unavailable", error=str(exc))
            return None

        if rows:
            ranked = [_member_and_distance(r) for r in rows]
            ids = [member for member, _ in ranked]
            result = await db.execute(
                select(Responder).where(
                    Responder.id.in_(ids),
                    Responder.type == required,
                    Responder.status == ResponderStatus.AVAILABLE,
                )
                # Claim-safe under concurrency: lock the candidate rows and skip
                # any a parallel dispatcher is already holding, so two incidents
                # can never be assigned the same responder.
                .with_for_update(skip_locked=True)
            )
            available = {r.id: r for r in result.scalars().all()}
            for member, distance in ranked:  # nearest first
                if member in available:
                    return ResponderMatch(
                        responder=available[member], distance_m=distance
                    )

        radius *= 2  # widen and retry

    return None
