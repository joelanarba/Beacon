"""Triage: severity → bus priority, and emergency type → who responds.

Severity already knows its own numeric priority (``Severity.numeric_priority``);
this module is the single place the dispatch side reads it, plus the mapping from
emergency type to the responder discipline and whether a hospital bed is needed.
"""

from __future__ import annotations

from channels.base import EmergencyType, Severity
from models.db import ResponderType


def severity_to_priority(severity: Severity) -> int:
    """Higher number = dequeued first (maps onto the RabbitMQ message priority)."""
    return severity.numeric_priority()


_REQUIRED_TYPE: dict[EmergencyType, ResponderType] = {
    EmergencyType.MEDICAL: ResponderType.AMBULANCE,
    EmergencyType.FIRE: ResponderType.FIRE,
    EmergencyType.SECURITY: ResponderType.POLICE,
    EmergencyType.OTHER: ResponderType.VOLUNTEER,
}


def required_responder_type(emergency_type: EmergencyType) -> ResponderType:
    """The responder discipline that should handle this emergency type."""
    return _REQUIRED_TYPE[emergency_type]


def needs_hospital(emergency_type: EmergencyType) -> bool:
    """Only medical emergencies also need a hospital bed reserved."""
    return emergency_type is EmergencyType.MEDICAL
