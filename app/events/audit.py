"""Append-only audit trail.

Every incident state transition writes one ``EventLog`` row — the dataset that
powers the dispatcher activity feed and the Grafana timeline. Payloads must be
JSON-safe (primitives / strings), since the column is JSONB; serialise datetimes
to ISO strings before passing them in.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from models.db import EventLog


async def record_event_log(
    session: AsyncSession,
    incident_id: str | None,
    event_type: str,
    payload: dict | None = None,
) -> None:
    """Add an audit row. The caller owns the commit."""
    session.add(
        EventLog(incident_id=incident_id, event_type=event_type, payload=payload or {})
    )
