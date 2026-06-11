"""Pydantic v2 request/response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------

class InitiateCallRequest(BaseModel):
    carbon_username: str
    message: str = ""  # optional Gemini system-prompt seed


class InitiateCallResponse(BaseModel):
    call_id: str
    status: Literal["ringing"]


class AnswerCallRequest(BaseModel):
    ring_token: str


class AnswerCallResponse(BaseModel):
    call_id: str
    livekit_url: str
    livekit_token: str
    silicon_display_name: str


class HangupRequest(BaseModel):
    ring_token: str = ""  # required from mobile; omit when Silicon hangs up via API key


class CallStatusResponse(BaseModel):
    call_id: str
    status: Literal["ringing", "active", "ended", "missed", "failed"]
    silicon_id: str
    carbon_username: str
    initiated_at: datetime
    answered_at: datetime | None = None
    ended_at: datetime | None = None


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------

class TranscriptTurn(BaseModel):
    speaker: Literal["carbon", "silicon"]
    text: str
    timestamp: datetime


class TranscriptResponse(BaseModel):
    call_id: str
    status: str
    summary: str
    turns: list[TranscriptTurn]


# ---------------------------------------------------------------------------
# Callback payload (ring → silicon-stemcell on call end)
# ---------------------------------------------------------------------------

class CallEndedCallback(BaseModel):
    call_id: str
    status: str
    summary: str
    turns: list[TranscriptTurn]
