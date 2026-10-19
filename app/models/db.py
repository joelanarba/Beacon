"""SQLAlchemy 2.0 async ORM: engine, session, and the Beacon domain model.

Design notes:
- The shared domain enums (``Severity``, ``EmergencyType``, ``ChannelKind``)
  live in ``channels/base.py`` and are reused here; the persistence-only enums
  are defined below.
- Primary keys are UUIDs (matching ``IncidentEvent.incident_id``), except
  ``EventLog`` which uses an autoincrement BigInteger for natural append-order
  (the audit/replay dataset).
- Enum values use VARCHAR columns; the versioned migrations define their checks.
- A ``Responder`` has no lat/lng here; its live position lives in Redis GEO.
  ``Hospital`` has a fixed location, and an ``Incident`` may have one.
- ``NullPool`` is used under pytest so tests don't leak
  connections across event loops.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Index,
    LargeBinary,
    String,
    Text,
    Uuid,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool

from channels.base import ChannelKind, EmergencyType, Severity
from config import get_settings


# --------------------------------------------------------------------------- #
# Persistence-only enums
# --------------------------------------------------------------------------- #
class IncidentStatus(str, Enum):
    REPORTED = "REPORTED"
    ASSIGNED = "ASSIGNED"
    EN_ROUTE = "EN_ROUTE"
    RESOLVED = "RESOLVED"


class ResponderType(str, Enum):
    AMBULANCE = "AMBULANCE"
    FIRE = "FIRE"
    POLICE = "POLICE"
    VOLUNTEER = "VOLUNTEER"


class ResponderStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    ASSIGNED = "ASSIGNED"
    OFFLINE = "OFFLINE"


class ReachableChannel(str, Enum):
    """How a party can be reached for outbound notification."""

    APP = "APP"
    SMS = "SMS"


class UserRole(str, Enum):
    DISPATCHER = "DISPATCHER"
    ADMIN = "ADMIN"


def _uuid_str() -> str:
    return str(uuid.uuid4())


def _enum_col(enum_cls: type[Enum]):
    return SAEnum(enum_cls, native_enum=False, length=20)


# --------------------------------------------------------------------------- #
# Base + models
# --------------------------------------------------------------------------- #
class Base(DeclarativeBase):
    pass


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=_uuid_str
    )
    reporter_contact: Mapped[str] = mapped_column(String(64), default="")
    emergency_type: Mapped[EmergencyType] = mapped_column(_enum_col(EmergencyType))
    severity: Mapped[Severity] = mapped_column(_enum_col(Severity))
    status: Mapped[IncidentStatus] = mapped_column(
        _enum_col(IncidentStatus), default=IncidentStatus.REPORTED
    )
    source_channel: Mapped[ChannelKind] = mapped_column(_enum_col(ChannelKind))
    description: Mapped[str] = mapped_column(Text, default="")
    ingest_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))

    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    area_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    media_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Responder(Base):
    __tablename__ = "responders"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=_uuid_str
    )
    name: Mapped[str] = mapped_column(String(128))
    type: Mapped[ResponderType] = mapped_column(_enum_col(ResponderType))
    status: Mapped[ResponderStatus] = mapped_column(
        _enum_col(ResponderStatus), default=ResponderStatus.AVAILABLE
    )
    contact: Mapped[str] = mapped_column(String(64))
    reachable_channel: Mapped[ReachableChannel] = mapped_column(
        _enum_col(ReachableChannel), default=ReachableChannel.APP
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Hospital(Base):
    __tablename__ = "hospitals"
    __table_args__ = (
        CheckConstraint(
            "total_capacity >= 0 AND available_capacity >= 0 "
            "AND available_capacity <= total_capacity",
            name="ck_hospitals_capacity",
        ),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=_uuid_str
    )
    name: Mapped[str] = mapped_column(String(128))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    total_capacity: Mapped[int] = mapped_column(Integer, default=0)
    available_capacity: Mapped[int] = mapped_column(Integer, default=0)
    specialties: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (UniqueConstraint("incident_id", name="uq_assignment_incident"),)

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=_uuid_str
    )
    incident_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("incidents.id"), index=True
    )
    responder_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("responders.id"), index=True
    )
    hospital_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("hospitals.id"), nullable=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "uq_assignment_active_responder",
    Assignment.responder_id,
    unique=True,
    postgresql_where=Assignment.released_at.is_(None),
)


class EventOutbox(Base):
    """Durable publication intent, committed with the corresponding state change."""

    __tablename__ = "event_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", "delivery_attempt", name="uq_outbox_delivery"),
        CheckConstraint("delivery_attempt >= 0", name="ck_outbox_attempt"),
        Index("ix_outbox_pending", "published_at", "available_at"),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=_uuid_str
    )
    event_id: Mapped[str] = mapped_column(String(200))
    routing_key: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    delivery_attempt: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publish_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(128))


class DeadLetter(Base):
    """Terminal consumer failure; retained for inspection rather than discarded."""

    __tablename__ = "dead_letters"
    __table_args__ = (
        UniqueConstraint("consumer", "event_id", name="uq_dead_letter_event"),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=_uuid_str
    )
    consumer: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[str] = mapped_column(String(200))
    routing_key: Mapped[str] = mapped_column(String(64))
    body: Mapped[bytes] = mapped_column(LargeBinary)
    delivery_attempt: Mapped[int] = mapped_column(Integer)
    error_type: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EventLog(Base):
    """Application audit records for incident acceptance and state transitions."""

    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), index=True, nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=_uuid_str
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        _enum_col(UserRole), default=UserRole.DISPATCHER
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RefreshToken(Base):
    """One row per issued refresh token; rotation revokes the old jti."""

    __tablename__ = "refresh_tokens"

    jti: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# --------------------------------------------------------------------------- #
# Engine + session
# --------------------------------------------------------------------------- #
def _make_engine():
    settings = get_settings()
    kwargs: dict = {"echo": False, "future": True}
    # NullPool under pytest: avoid connections bound to a closed event loop.
    if "pytest" in sys.modules:
        kwargs["poolclass"] = NullPool
    return create_async_engine(settings.database_url, **kwargs)


engine = _make_engine()
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one AsyncSession per request."""
    async with async_session() as session:
        yield session
