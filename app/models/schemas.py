"""Pydantic v2 API contracts (request/response shapes).

These are the I/O contracts at the HTTP boundary — distinct from the SQLAlchemy
ORM models in ``db.py``. Read models set ``from_attributes=True`` so they can be
built directly from ORM rows.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from channels.base import ChannelKind, EmergencyType, Severity
from models.db import (
    IncidentStatus,
    ReachableChannel,
    ResponderStatus,
    ResponderType,
    UserRole,
)


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None
    role: UserRole = UserRole.DISPATCHER


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    full_name: str | None = None
    role: UserRole
    is_active: bool
    created_at: datetime


# --------------------------------------------------------------------------- #
# Domain entities (read models)
# --------------------------------------------------------------------------- #
class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    reporter_contact: str
    emergency_type: EmergencyType
    severity: Severity
    status: IncidentStatus
    source_channel: ChannelKind
    description: str
    latitude: float | None = None
    longitude: float | None = None
    area_label: str | None = None
    media_url: str | None = None
    created_at: datetime
    updated_at: datetime


class ResponderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    type: ResponderType
    status: ResponderStatus
    contact: str
    reachable_channel: ReachableChannel
    created_at: datetime


class HospitalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    latitude: float
    longitude: float
    total_capacity: int
    available_capacity: int
    specialties: list[str]
    created_at: datetime


class AssignmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    incident_id: str
    responder_id: str
    hospital_id: str | None = None
    assigned_at: datetime


# --------------------------------------------------------------------------- #
# Ingestion (channel-facing contracts)
# --------------------------------------------------------------------------- #
class IngestAppRequest(BaseModel):
    """Rich app/web report. The app channel can fill the whole event; severity
    is optional and defaults from the emergency type when omitted."""

    emergency_type: EmergencyType
    description: str = ""
    reporter_contact: str = ""
    severity: Severity | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    area_label: str | None = None
    media_url: str | None = None


class IngestAck(BaseModel):
    """Acknowledgement returned to a data-channel reporter."""

    incident_id: str
    status: IncidentStatus
    source_channel: ChannelKind
    severity: Severity


class StatusUpdate(BaseModel):
    """Dispatcher request to advance an incident (EN_ROUTE / RESOLVED)."""

    status: IncidentStatus
