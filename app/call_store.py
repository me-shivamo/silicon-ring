"""In-memory call state store. One process, one server — no persistence needed.

Each active call is a dict:
{
    "call_id": str,
    "silicon_id": str,
    "silicon_display_name": str,
    "carbon_username": str,
    "carbon_id": str,          # Glass ULID
    "glass_room_id": str,      # Glass Room ULID for posting call events
    "livekit_room": str,
    "message": str,            # Gemini system-prompt seed
    "status": ringing|active|ended|missed|failed,
    "initiated_at": datetime,
    "answered_at": datetime|None,
    "ended_at": datetime|None,
    "turns": list[dict],       # accumulated transcript {speaker, text, timestamp}
    "summary": str,
    "callback_url": str,
    "_answer_event": asyncio.Event,  # set when Carbon answers
    "_end_event": asyncio.Event,     # set when either party hangs up
}
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

# call_id → call dict
_calls: dict[str, dict[str, Any]] = {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create(
    call_id: str,
    silicon_id: str,
    silicon_display_name: str,
    carbon_username: str,
    carbon_id: str,
    glass_room_id: str,
    livekit_room: str,
    message: str,
    callback_url: str = "",
) -> dict[str, Any]:
    call = {
        "call_id": call_id,
        "silicon_id": silicon_id,
        "silicon_display_name": silicon_display_name,
        "carbon_username": carbon_username,
        "carbon_id": carbon_id,
        "glass_room_id": glass_room_id,
        "livekit_room": livekit_room,
        "message": message,
        "status": "ringing",
        "initiated_at": utcnow(),
        "answered_at": None,
        "ended_at": None,
        "turns": [],
        "summary": "",
        "callback_url": callback_url,
        "_answer_event": asyncio.Event(),
        "_end_event": asyncio.Event(),
    }
    _calls[call_id] = call
    return call


def get(call_id: str) -> dict[str, Any] | None:
    return _calls.get(call_id)


def mark_active(call_id: str) -> None:
    call = _calls.get(call_id)
    if call:
        call["status"] = "active"
        call["answered_at"] = utcnow()
        call["_answer_event"].set()


def mark_ended(call_id: str, status: str = "ended") -> None:
    call = _calls.get(call_id)
    if call:
        call["status"] = status
        call["ended_at"] = utcnow()
        call["_end_event"].set()


def append_turn(call_id: str, speaker: str, text: str) -> None:
    call = _calls.get(call_id)
    if call:
        call["turns"].append({"speaker": speaker, "text": text, "timestamp": utcnow()})


def set_summary(call_id: str, summary: str) -> None:
    call = _calls.get(call_id)
    if call:
        call["summary"] = summary


def remove(call_id: str) -> None:
    _calls.pop(call_id, None)


def all_calls() -> list[dict[str, Any]]:
    return list(_calls.values())
