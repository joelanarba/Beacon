"""Hospital matching via Postgres (capacity + specialty).

Unlike responders, hospitals are fixed sites with a fixed location, so they live
entirely in Postgres. A medical incident reserves a bed: we filter to hospitals
with spare capacity (and the required specialty, when one is given), pick the
nearest, and decrement ``available_capacity`` to hold the bed. The caller owns
the commit, so the reservation is part of the same transaction as the Assignment.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dispatch.geo import haversine_m
from models.db import Hospital


@dataclass
class HospitalMatch:
    hospital: Hospital
    distance_m: float


async def match_hospital(
    db: AsyncSession,
    *,
    latitude: float,
    longitude: float,
    required_specialty: str | None = None,
) -> HospitalMatch | None:
    """Nearest hospital with spare capacity (and specialty), bed reserved."""
    stmt = select(Hospital).where(Hospital.available_capacity > 0)
    if required_specialty:
        # 'specialty' = ANY(hospitals.specialties)
        stmt = stmt.where(Hospital.specialties.any(required_specialty))
    # Lock candidates and skip any a concurrent dispatcher is reserving, so the
    # last bed at a hospital can't be double-booked.
    stmt = stmt.with_for_update(skip_locked=True)

    candidates = (await db.execute(stmt)).scalars().all()
    if not candidates:
        return None

    chosen = min(
        candidates,
        key=lambda h: haversine_m(latitude, longitude, h.latitude, h.longitude),
    )
    distance = haversine_m(latitude, longitude, chosen.latitude, chosen.longitude)
    chosen.available_capacity -= 1  # reserve the bed
    return HospitalMatch(hospital=chosen, distance_m=distance)
