"""Channel adapters: every channel normalises raw input to ONE IncidentEvent.

These are pure unit tests, no DB or Redis. They check the menu walk, the SMS
keyword parse, and that the same logical report over all three channels
converges on the same normalised event, with only location capability differing.
"""

from __future__ import annotations

from channels.app_channel import AppChannel
from channels.base import (
    ChannelKind,
    EmergencyType,
    IncidentEvent,
    PartialSession,
    Severity,
)
from channels.sms_channel import SMSChannel
from channels.ussd_channel import CON, END, USSDChannel

_SESSION = {"sessionId": "s1", "phoneNumber": "+233200000001"}


def _ussd(text: str) -> dict:
    return {**_SESSION, "text": text}


# --------------------------------------------------------------------------- #
# USSD menu walk
# --------------------------------------------------------------------------- #
async def test_ussd_full_walk_emits_event():
    ch = USSDChannel()

    root = await ch.parse(_ussd(""))
    assert isinstance(root, PartialSession)
    assert root.reply.startswith(CON) and not root.done

    picked = await ch.parse(_ussd("2"))  # 2 = FIRE
    assert isinstance(picked, PartialSession) and not picked.done

    located = await ch.parse(_ussd("2*Osu"))
    assert isinstance(located, PartialSession) and "Confirm" in located.reply

    event = await ch.parse(_ussd("2*Osu*1"))  # 1 = confirm
    assert isinstance(event, IncidentEvent)
    assert event.source_channel is ChannelKind.USSD
    assert event.emergency_type is EmergencyType.FIRE
    assert event.severity is Severity.CRITICAL
    assert event.area_label == "Osu"
    assert not event.has_precise_location()  # no GPS on USSD
    assert event.reporter_contact == "+233200000001"


async def test_ussd_invalid_type_ends_session():
    r = await USSDChannel().parse(_ussd("9"))
    assert isinstance(r, PartialSession) and r.done and r.reply.startswith(END)


async def test_ussd_cancel_ends_session():
    r = await USSDChannel().parse(_ussd("1*Osu*2"))  # 2 = cancel at confirm
    assert isinstance(r, PartialSession) and r.done and r.reply.startswith(END)


# --------------------------------------------------------------------------- #
# SMS keyword parse
# --------------------------------------------------------------------------- #
async def test_sms_keyword_parse_emits_event():
    event = await SMSChannel().parse(
        {"from": "+233200000222", "text": "MED pregnant woman collapsed"}
    )
    assert isinstance(event, IncidentEvent)
    assert event.source_channel is ChannelKind.SMS
    assert event.emergency_type is EmergencyType.MEDICAL
    assert event.severity is Severity.URGENT
    assert "pregnant" in event.description


async def test_sms_malformed_returns_help_reply():
    r = await SMSChannel().parse(
        {"from": "+233200000222", "text": "my house is burning"}
    )
    assert isinstance(r, PartialSession)
    assert r.done
    assert "FIRE" in r.reply  # nudges the reporter to the keyword format


# --------------------------------------------------------------------------- #
# App (rich data) channel
# --------------------------------------------------------------------------- #
async def test_app_channel_full_event():
    event = await AppChannel().parse(
        {
            "emergency_type": "MEDICAL",
            "description": "cardiac arrest",
            "reporter_contact": "user-1",
            "latitude": 5.56,
            "longitude": -0.2,
            "severity": "CRITICAL",
        }
    )
    assert isinstance(event, IncidentEvent)
    assert event.has_precise_location()
    assert event.severity is Severity.CRITICAL  # explicit override respected


# --------------------------------------------------------------------------- #
# Convergence: every channel yields the same event shape
# --------------------------------------------------------------------------- #
async def test_all_channels_converge_on_one_event_shape():
    fire_app = await AppChannel().parse(
        {
            "emergency_type": "FIRE",
            "description": "market ablaze",
            "latitude": 5.55,
            "longitude": -0.19,
        }
    )
    fire_sms = await SMSChannel().parse(
        {"from": "+2330000", "text": "FIRE market ablaze"}
    )
    fire_ussd = await USSDChannel().parse(_ussd("2*Market*1"))

    for ev in (fire_app, fire_sms, fire_ussd):
        assert isinstance(ev, IncidentEvent)
        assert ev.emergency_type is EmergencyType.FIRE
        assert ev.severity is Severity.CRITICAL  # classified identically

    # Capability gradient is encoded by which location fields are present.
    assert fire_app.has_precise_location()
    assert fire_ussd.area_label == "Market" and not fire_ussd.has_precise_location()
    assert not fire_sms.has_precise_location() and fire_sms.area_label is None
