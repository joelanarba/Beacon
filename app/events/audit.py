"""Append-only audit trail.

Acceptance and lifecycle updates write ``EventLog`` rows in their database
transactions. Payloads must be JSON-safe because the column is JSONB; serialize
datetimes to ISO strings before passing them in. The caller owns the commit.
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
