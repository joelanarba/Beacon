"""Event payload contracts (the wire format on the bus).

These pydantic models are what actually travels through RabbitMQ — distinct from
the in-process ``IncidentEvent`` dataclass (a channel-parse result) and the
SQLAlchemy ORM rows (persistence). Keeping them separate means the bus schema
can evolve without dragging either of the other two along.

Routing keys follow ``incident.<lifecycle>`` so a topic exchange can fan out by
stage (dispatch consumes ``incident.reported``; notification consumes the rest).
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from channels.base import ChannelKind, EmergencyType, IncidentEvent, Severity
from models.db import IncidentStatus, ReachableChannel

# Routing keys (topic exchange).
RK_REPORTED = "incident.reported"
RK_ASSIGNED = "incident.assigned"
RK_EN_ROUTE = "incident.en_route"
RK_RESOLVED = "incident.resolved"


class IncidentReported(BaseModel):
    """Published off the ingestion path; consumed by the dispatch engine."""

    incident_id: str
    source_channel: ChannelKind
    emergency_type: EmergencyType
    severity: Severity
    reporter_contact: str = ""
    description: str = ""
    latitude: float | None = None
    longitude: float | None = None
    area_label: str | None = None
    media_url: str | None = None
    reported_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_event(cls, event: IncidentEvent) -> IncidentReported:
        return cls(
            incident_id=event.incident_id,
            source_channel=event.source_channel,
            emergency_type=event.emergency_type,
            severity=event.severity,
            reporter_contact=event.reporter_contact,
            description=event.description,
            latitude=event.latitude,
            longitude=event.longitude,
            area_label=event.area_label,
            media_url=event.media_url,
        )


class IncidentAssigned(BaseModel):
    """Emitted by the dispatch engine; consumed by the notification fan-out.

    Carries the responder's contact + reachable channel so the notifier can route
    without a second DB lookup.
    """

    incident_id: str
    responder_id: str
    responder_name: str
    responder_contact: str
    reachable_channel: ReachableChannel
    hospital_id: str | None = None
    hospital_name: str | None = None
    source_channel: ChannelKind
    severity: Severity
    match_radius_m: float | None = None
    assigned_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class IncidentStatusChanged(BaseModel):
    """Lifecycle transition (EN_ROUTE / RESOLVED)."""

    incident_id: str
    status: IncidentStatus
    changed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
