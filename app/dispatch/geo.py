"""Geospatial helpers for dispatch.

Two jobs:

1. resolve_location: turn whatever location a channel captured into usable
   coordinates. The app channel gives precise GPS, USSD gives a coarse area
   label, and SMS often gives neither. A report with no GPS still gets matched
   by resolving its area label, or falling back to the city centroid. This area
   table is a simple stand-in for a real geocoder.

2. haversine_m: great-circle distance in metres, used to rank hospitals (Redis
   already ranks responders by distance for us).
"""

from __future__ import annotations

import math

# Coarse area -> (lat, lng). Accra-area approximations matching the seeded
# responder/hospital footprint. A real deployment swaps this for a geocoder.
_AREAS: dict[str, tuple[float, float]] = {
    "ACCRA": (5.5600, -0.2057),
    "OSU": (5.5558, -0.1769),
    "LABADI": (5.5575, -0.1560),
    "ADABRAKA": (5.5650, -0.2120),
    "RIDGE": (5.5650, -0.1969),
    "DANSOMAN": (5.5400, -0.2530),
    "ACHIMOTA": (5.6190, -0.2270),
    "MADINA": (5.6836, -0.1664),
    "TESHIE": (5.5840, -0.1050),
    "TEMA": (5.6390, -0.0170),
}
_DEFAULT_CENTROID = (5.5600, -0.2057)  # Accra


def resolve_location(
    latitude: float | None,
    longitude: float | None,
    area_label: str | None,
) -> tuple[float, float]:
    """Best-available coordinates: precise GPS > known area > city centroid."""
    if latitude is not None and longitude is not None:
        return latitude, longitude
    if area_label:
        coords = _AREAS.get(area_label.strip().upper())
        if coords is not None:
            return coords
    return _DEFAULT_CENTROID


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points, in metres."""
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))
