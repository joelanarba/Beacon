"""Normalize an app report into a complete IncidentEvent.

HTTP validation happens in IngestAppRequest. App reports provide the required
fields in one request, so this channel does not create a partial session.
"""

from __future__ import annotations

from .base import (
    Channel,
    ChannelKind,
    EmergencyType,
    IncidentEvent,
    Severity,
    default_severity,
)


class AppChannel(Channel):
    kind = ChannelKind.APP

    async def parse(self, raw: dict) -> IncidentEvent:
        etype = EmergencyType(raw["emergency_type"])
        severity = (
            Severity(raw["severity"])
            if raw.get("severity") is not None
            else default_severity(etype)
        )
        return IncidentEvent(
            source_channel=ChannelKind.APP,
            reporter_contact=str(raw.get("reporter_contact", "") or ""),
            emergency_type=etype,
            severity=severity,
            description=str(raw.get("description", "") or ""),
            latitude=raw.get("latitude"),
            longitude=raw.get("longitude"),
            area_label=raw.get("area_label"),
            media_url=raw.get("media_url"),
        )
