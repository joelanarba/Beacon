"""
App channel — the rich data channel (top of the connectivity gradient).

A smartphone/web client on mobile data can fill the whole ``IncidentEvent``:
precise GPS, a media URL, a stable contact. It always produces a *complete*
event in one shot (no session walk), so ``parse`` never returns a
``PartialSession``.

The raw input is already validated upstream by the ``IngestAppRequest`` pydantic
contract; this channel just normalises it into the shared ``IncidentEvent`` so
the dispatch engine stays channel-agnostic.
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
