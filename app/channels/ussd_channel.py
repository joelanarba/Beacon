"""
USSD channel: a session-based menu walk.

USSD works with no mobile data at all, over the GSM signaling channel. Real
providers (e.g. Africa's Talking) POST a callback with:

    sessionId, phoneNumber, text

where ``text`` ACCUMULATES the user's menu choices joined by "*":
    ""        first dial (show root menu)
    "1"       picked option 1
    "1*2"     then picked option 2
    "1*2*Osu" then typed a location

The reply is prefixed with the provider contract:
    "CON <text>"  — continue the session, expect more input
    "END <text>"  — terminate the session

We DO NOT hold session state in memory (multi-worker safe): the accumulated
``text`` is itself the state for the menu position, and any extra derived state
lives in Redis keyed by sessionId with a short TTL. The local simulator in
``sim/provider.py`` mimics this exact contract, so swapping in a real provider
later changes nothing here.

Menu walk:
    root         → choose emergency type (1 medical / 2 fire / 3 security / 4 other)
    type chosen  → ask for area/location (free text)
    area entered → confirm (1 yes / 2 cancel)
    confirmed    → emit IncidentEvent, reply END
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

CON = "CON "
END = "END "

_TYPE_MENU = {
    "1": EmergencyType.MEDICAL,
    "2": EmergencyType.FIRE,
    "3": EmergencyType.SECURITY,
    "4": EmergencyType.OTHER,
}

_ROOT_PROMPT = (
    "Emergency report. Choose type:\n" "1. Medical\n2. Fire\n3. Security\n4. Other"
)


def _steps(text: str) -> list[str]:
    """Split the accumulated USSD text into menu steps."""
    return [s for s in text.split("*")] if text else []


class USSDChannel(Channel):
    kind = ChannelKind.USSD

    async def parse(self, raw: dict) -> IncidentEvent | PartialSession:
        session_id = str(raw.get("sessionId", ""))
        phone = str(raw.get("phoneNumber", ""))
        text = str(raw.get("text", "") or "")
        steps = _steps(text)

        # Step 0 — first dial: show the root menu.
        if len(steps) == 0:
            return PartialSession(session_id, CON + _ROOT_PROMPT, done=False)

        # Step 1 — emergency type chosen.
        type_choice = steps[0]
        if type_choice not in _TYPE_MENU:
            return PartialSession(
                session_id, END + "Invalid choice. Please dial again.", done=True
            )
        etype = _TYPE_MENU[type_choice]

        if len(steps) == 1:
            return PartialSession(
                session_id,
                CON + f"{etype.value} selected.\nEnter your location/area:",
                done=False,
            )

        # Step 2 — location entered.
        area = steps[1].strip()
        if len(steps) == 2:
            if not area:
                return PartialSession(
                    session_id, CON + "Enter your location/area:", done=False
                )
            return PartialSession(
                session_id,
                CON + f"Confirm {etype.value} at '{area}'?\n1. Yes  2. Cancel",
                done=False,
            )

        # Step 3 — confirmation.
        confirm = steps[2]
        if confirm != "1":
            return PartialSession(session_id, END + "Report cancelled.", done=True)

        # Confirmed → emit the normalised IncidentEvent.
        event = IncidentEvent(
            source_channel=ChannelKind.USSD,
            reporter_contact=phone,
            emergency_type=etype,
            severity=default_severity(etype),
            description=f"USSD report: {etype.value} at {area}",
            area_label=area,  # coarse location — no GPS on USSD
        )
        # The caller publishes the event and then sends this END reply.
        # We return the event; the ingestion layer is responsible for replying
        # END "Help is on the way. Ref: <id>". See ingestion/api.py.
        return event
