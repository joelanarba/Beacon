"""
SMS channel — async, store-and-forward, minimal capability.

Inbound SMS is the lowest rung of the connectivity gradient: a single
store-and-forward text on the GSM signaling channel, no session, no GPS. A real
provider (Africa's Talking) delivers ``(from, text)``; the local simulator
mirrors that exact shape.

Parse contract: a leading keyword selects the emergency type, and the remaining
free text becomes the description. Unparseable input (no recognised keyword)
yields a ``PartialSession`` carrying a helpful reply and emits NO incident — the
same "reply, don't dispatch" shape USSD uses for a terminal prompt.

    FIRE  Tema community 5 market ablaze   ->  FIRE,     desc="Tema community ..."
    MED   pregnant woman collapsed         ->  MEDICAL,  desc="pregnant woman ..."
    HELP  trapped under rubble             ->  OTHER,    desc="trapped under ..."
    (no keyword)                           ->  PartialSession(help reply)
"""

from __future__ import annotations

from .base import (
    Channel,
    ChannelKind,
    EmergencyType,
    IncidentEvent,
    PartialSession,
    default_severity,
)

# Leading keyword -> emergency type. Synonyms keep the contract forgiving for a
# stressed reporter on a feature phone.
_KEYWORDS = {
    "FIRE": EmergencyType.FIRE,
    "MED": EmergencyType.MEDICAL,
    "MEDICAL": EmergencyType.MEDICAL,
    "AMBULANCE": EmergencyType.MEDICAL,
    "SECURITY": EmergencyType.SECURITY,
    "POLICE": EmergencyType.SECURITY,
    "HELP": EmergencyType.OTHER,
    "SOS": EmergencyType.OTHER,
}

_HELP_REPLY = (
    "Beacon: start your message with an emergency keyword so we can route it: "
    "FIRE, MED, SECURITY, or HELP. Example: 'FIRE Osu market, near the bank'."
)


class SMSChannel(Channel):
    kind = ChannelKind.SMS

    async def parse(self, raw: dict) -> IncidentEvent | PartialSession:
        sender = str(raw.get("from", "") or "")
        body = str(raw.get("text", "") or "").strip()

        if not body:
            return PartialSession(sender, _HELP_REPLY, done=True)

        keyword, _, remainder = body.partition(" ")
        etype = _KEYWORDS.get(keyword.upper())
        if etype is None:
            # No recognised keyword — ask for the format, emit nothing.
            return PartialSession(sender, _HELP_REPLY, done=True)

        description = remainder.strip() or f"{etype.value} emergency (no detail given)"
        return IncidentEvent(
            source_channel=ChannelKind.SMS,
            reporter_contact=sender,
            emergency_type=etype,
            severity=default_severity(etype),
            description=description,
        )
