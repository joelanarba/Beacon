"""Dispatcher-facing incident endpoints (authenticated).

A dispatcher advances an incident through its lifecycle (ASSIGNED → EN_ROUTE →
RESOLVED). Each transition writes an ``EventLog`` row and publishes the matching
``incident.<status>`` event, which the notification consumer propagates live to
every connected dispatcher. Resolving an incident also frees the responder and
restores the reserved hospital bed.

These routes are NOT in the ingestion open-list, so they sit behind
``AuthMiddleware`` and require a dispatcher/admin access token.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import require_role
from events.audit import record_event_log
from events.schemas import RK_EN_ROUTE, RK_RESOLVED, IncidentStatusChanged
from models.db import (
    Assignment,
    Hospital,
    Incident,
    IncidentStatus,
    Responder,
    ResponderStatus,
    get_session,
)
from models.schemas import IncidentRead, StatusUpdate
from utils.logging import get_logger

log = get_logger("beacon.incidents")
router = APIRouter(prefix="/incidents", tags=["incidents"])

_DISPATCH_ROLES = ("DISPATCHER", "ADMIN")
_ALLOWED_TRANSITIONS = {IncidentStatus.EN_ROUTE, IncidentStatus.RESOLVED}
_ROUTING_KEY = {
    IncidentStatus.EN_ROUTE: RK_EN_ROUTE,
    IncidentStatus.RESOLVED: RK_RESOLVED,
}


@router.get("", response_model=list[IncidentRead])
async def list_incidents(
    _principal=Depends(require_role(*_DISPATCH_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> list[Incident]:
    rows = await session.execute(
        select(Incident).order_by(Incident.created_at.desc()).limit(100)
    )
    return list(rows.scalars().all())


@router.post("/{incident_id}/status", response_model=IncidentRead)
async def advance_status(
    incident_id: str,
    body: StatusUpdate,
    request: Request,
    _principal=Depends(require_role(*_DISPATCH_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> Incident:
    if body.status not in _ALLOWED_TRANSITIONS:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            detail="This endpoint only advances to EN_ROUTE or RESOLVED",
        )
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Incident not found")

    incident.status = body.status
    await record_event_log(
        session, incident_id, _ROUTING_KEY[body.status], {"status": body.status.value}
    )
    if body.status is IncidentStatus.RESOLVED:
        await _release_resources(session, incident_id)
    await session.commit()
    await session.refresh(incident)

    # Propagate over the bus (fail-open; no-op when the bus is absent in tests).
    bus = getattr(request.app.state, "bus", None)
    if bus is not None:
        try:
            await bus.publish(
                _ROUTING_KEY[body.status],
                IncidentStatusChanged(incident_id=incident_id, status=body.status),
                priority=1,  # lifecycle updates ride below new reports
            )
        except Exception as exc:  # noqa: BLE001 — fail-open on bus errors
            log.warning("incident.publish_failed", error=str(exc))

    log.info(
        "incident.status_advanced", incident_id=incident_id, status=body.status.value
    )
    return incident


async def _release_resources(session: AsyncSession, incident_id: str) -> None:
    """On RESOLVED: free the assigned responder(s) and restore hospital beds."""
    result = await session.execute(
        select(Assignment).where(Assignment.incident_id == incident_id)
    )
    for assignment in result.scalars().all():
        responder = await session.get(Responder, assignment.responder_id)
        if responder is not None:
            responder.status = ResponderStatus.AVAILABLE
        if assignment.hospital_id:
            hospital = await session.get(Hospital, assignment.hospital_id)
            if hospital is not None:
                hospital.available_capacity += 1
