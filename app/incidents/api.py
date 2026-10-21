"""Dispatcher-facing incident endpoints (authenticated).

A dispatcher advances an incident through its lifecycle (ASSIGNED -> EN_ROUTE ->
RESOLVED). Each transition writes an ``EventLog`` row and publishes the matching
``incident.<status>`` event, which the notification consumer propagates live to
every connected dispatcher. Resolving an incident also frees the responder and
restores the reserved hospital bed.

These routes are NOT in the ingestion open-list, so they sit behind
``AuthMiddleware`` and require a dispatcher/admin access token.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import require_role
from dispatch.engine import refresh_active_responders
from events.audit import record_event_log
from events.outbox import enqueue_event
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
_NEXT_STATUS = {
    IncidentStatus.ASSIGNED: {IncidentStatus.EN_ROUTE, IncidentStatus.RESOLVED},
    IncidentStatus.EN_ROUTE: {IncidentStatus.RESOLVED},
}
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
    incident_id: UUID,
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
    incident_id = str(incident_id)
    incident = (
        await session.execute(
            select(Incident)
            .where(Incident.id == incident_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if incident is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Incident not found")

    if incident.status == body.status:
        await session.commit()
        return incident
    if body.status not in _NEXT_STATUS.get(incident.status, set()):
        raise HTTPException(
            http_status.HTTP_409_CONFLICT, detail="Invalid incident state transition"
        )

    incident.status = body.status
    await record_event_log(
        session, incident_id, _ROUTING_KEY[body.status], {"status": body.status.value}
    )
    if body.status is IncidentStatus.RESOLVED:
        await _release_resources(session, incident_id)
    await enqueue_event(
        session,
        _ROUTING_KEY[body.status],
        IncidentStatusChanged(incident_id=incident_id, status=body.status),
        event_id=f"{_ROUTING_KEY[body.status]}:{incident_id}",
        priority=1,
    )
    await session.commit()
    await session.refresh(incident)
    try:
        await refresh_active_responders(session)
    except Exception as exc:
        log.warning("incident.gauge_refresh_failed", error_type=type(exc).__name__)

    log.info(
        "incident.status_advanced", incident_id=incident_id, status=body.status.value
    )
    return incident


async def _release_resources(session: AsyncSession, incident_id: str) -> None:
    """Release once, with lock order incident -> assignment -> responder -> hospital."""
    result = await session.execute(
        select(Assignment)
        .where(Assignment.incident_id == incident_id, Assignment.released_at.is_(None))
        .with_for_update()
    )
    for assignment in result.scalars().all():
        responder = (
            await session.execute(
                select(Responder)
                .where(Responder.id == assignment.responder_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if responder is not None and responder.status == ResponderStatus.ASSIGNED:
            responder.status = ResponderStatus.AVAILABLE
        if assignment.hospital_id:
            hospital = (
                await session.execute(
                    select(Hospital)
                    .where(Hospital.id == assignment.hospital_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if hospital is not None:
                hospital.available_capacity += 1
        assignment.released_at = datetime.now(UTC)
