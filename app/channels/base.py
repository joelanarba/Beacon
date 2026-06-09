"""
Channel abstraction shared by every ingress channel.

Every ingress channel (app, USSD, SMS) normalises its raw input into the same
``IncidentEvent``. Capability differences between channels show up as fields
present or absent on the event, not as a different downstream code path, so the
dispatch engine consumes IncidentEvent and stays channel-agnostic.

A channel parse can yield one of two things:
- ``IncidentEvent``   — a complete report, ready to publish to the bus.
- ``PartialSession``  — more input is needed (USSD menu walk in progress);
                        carries the reply to send back to the user.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    """Triage severity. Ordering matters — see numeric_priority()."""

    CRITICAL = "CRITICAL"
    URGENT = "URGENT"
    STANDARD = "STANDARD"

    def numeric_priority(self) -> int:
        """Map to a RabbitMQ message priority (higher = more urgent)."""
        return {"CRITICAL": 10, "URGENT": 5, "STANDARD": 1}[self.value]


class EmergencyType(str, Enum):
    MEDICAL = "MEDICAL"
    FIRE = "FIRE"
    SECURITY = "SECURITY"
    OTHER = "OTHER"


# Default severity per emergency type, used by the signaling channels (USSD/SMS)
# which cannot capture a precise severity from the reporter. The dispatcher /
# triage stage may refine this later. Single source of truth so every
# channel classifies the same logical report identically.
DEFAULT_SEVERITY: dict[EmergencyType, Severity] = {
    EmergencyType.MEDICAL: Severity.URGENT,
    EmergencyType.FIRE: Severity.CRITICAL,
    EmergencyType.SECURITY: Severity.URGENT,
    EmergencyType.OTHER: Severity.STANDARD,
}


def default_severity(etype: EmergencyType) -> Severity:
    """Classify a default severity from the emergency type."""
    return DEFAULT_SEVERITY[etype]


class ChannelKind(str, Enum):
    APP = "APP"  # data, real-time, bidirectional
    USSD = "USSD"  # GSM signaling, session, menu-driven
    SMS = "SMS"  # GSM signaling, async, store-and-forward


@dataclass
class IncidentEvent:
    """The single normalised report shape produced by every channel.

    Capability gradient is encoded by which optional fields are populated:
      - APP can fill everything (precise lat/lng, media, live contact).
      - USSD fills structured fields from menu choices (area, no media).
      - SMS may fill only emergency_type + free-text description.
    Downstream NEVER branches on source_channel except for audit/metrics.
    """

    incident_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_channel: ChannelKind = ChannelKind.APP
    reporter_contact: str = ""  # phone number or user id
    emergency_type: EmergencyType = EmergencyType.OTHER
    severity: Severity = Severity.STANDARD
    description: str = ""

    # Capability-dependent (may be None on signaling channels):
    latitude: float | None = None
    longitude: float | None = None
    area_label: str | None = None  # coarse location when no GPS
    media_url: str | None = None

    def has_precise_location(self) -> bool:
        return self.latitude is not None and self.longitude is not None


@dataclass
class PartialSession:
    """Returned mid-session (USSD) when more user input is required.

    ``reply`` is the text to send back. ``done`` False means keep the session
    open (provider gets a ``CON`` reply); a channel maps this to its contract.
    """

    session_id: str
    reply: str
    done: bool = False


class Channel(ABC):
    """Base class for all ingress channels."""

    kind: ChannelKind

    @abstractmethod
    async def parse(self, raw: dict) -> IncidentEvent | PartialSession:
        """Normalise raw channel input into an IncidentEvent or PartialSession.

        Implementations must be pure with respect to downstream: the only
        output that reaches the dispatch engine is a complete IncidentEvent.
        """
        raise NotImplementedError
