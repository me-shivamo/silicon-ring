"""Call lifecycle endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app import call_store, ring_token
from app.auth.silicon import get_silicon
from app.schemas import (
    AnswerCallRequest,
    AnswerCallResponse,
    CallStatusResponse,
    HangupRequest,
    InitiateCallRequest,
    InitiateCallResponse,
    TranscriptResponse,
    TranscriptTurn,
)
from app.services import livekit_service

log = logging.getLogger(__name__)
router = APIRouter(tags=["calls"])


@router.post("/calls/initiate", response_model=InitiateCallResponse, status_code=202)
async def initiate_call(
    body: InitiateCallRequest,
    silicon: dict = Depends(get_silicon),
) -> InitiateCallResponse:
    """Silicon triggers a voice call to a Carbon. Returns call_id immediately."""
    from app.services.call_manager import initiate_call as _initiate

    try:
        call = await _initiate(
            carbon_username=body.carbon_username,
            silicon_info=silicon,
            message=body.message,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return InitiateCallResponse(call_id=call["call_id"], status="ringing")


@router.post("/calls/{call_id}/answer", response_model=AnswerCallResponse)
async def answer_call(call_id: str, body: AnswerCallRequest) -> AnswerCallResponse:
    """Carbon accepted the call. Validates ring_token and returns LiveKit credentials."""
    verified_id = ring_token.verify(body.ring_token)
    if not verified_id or verified_id != call_id:
        raise HTTPException(status_code=401, detail="Invalid or expired ring token.")

    call = call_store.get(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found.")

    if call["status"] == "missed":
        raise HTTPException(status_code=410, detail="Call already ended (missed).")

    if call["status"] == "ended":
        raise HTTPException(status_code=410, detail="Call already ended.")

    if call["status"] not in ("ringing", "active"):
        raise HTTPException(status_code=409, detail=f"Call in unexpected state: {call['status']}")

    # Mark active (idempotent — Carbon may reconnect)
    if call["status"] == "ringing":
        call_store.mark_active(call_id)

    livekit_token = livekit_service.create_carbon_token(
        call["livekit_room"], call["carbon_username"]
    )

    return AnswerCallResponse(
        call_id=call_id,
        livekit_url=_livekit_url(),
        livekit_token=livekit_token,
        silicon_display_name=call["silicon_display_name"],
    )


@router.post("/calls/{call_id}/hangup", status_code=200)
async def hangup_call(
    call_id: str,
    body: HangupRequest = HangupRequest(),
    silicon: dict | None = Depends(_optional_silicon),
) -> dict:
    """Either party ends the call. Silicon uses its API key; Carbon uses ring_token."""
    call = call_store.get(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found.")

    if call["status"] in ("ended", "missed", "failed"):
        return {"ended": True}

    # Determine who is hanging up
    if silicon:
        # Authenticated Silicon — verify it owns this call
        if silicon.get("silicon_id") != call["silicon_id"]:
            raise HTTPException(status_code=403, detail="Not your call.")
    elif body.ring_token:
        verified_id = ring_token.verify(body.ring_token)
        if not verified_id or verified_id != call_id:
            raise HTTPException(status_code=401, detail="Invalid or expired ring token.")
    else:
        raise HTTPException(status_code=401, detail="Provide ring_token or X-Silicon-Key.")

    call_store.mark_ended(call_id, status="ended")
    log.info("Call %s hung up", call_id)
    return {"ended": True}


@router.get("/calls/{call_id}", response_model=CallStatusResponse)
async def call_status(
    call_id: str,
    silicon: dict = Depends(get_silicon),
) -> CallStatusResponse:
    """Poll call status."""
    call = call_store.get(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found.")
    if silicon.get("silicon_id") != call["silicon_id"]:
        raise HTTPException(status_code=403, detail="Not your call.")

    return CallStatusResponse(
        call_id=call["call_id"],
        status=call["status"],
        silicon_id=call["silicon_id"],
        carbon_username=call["carbon_username"],
        initiated_at=call["initiated_at"],
        answered_at=call.get("answered_at"),
        ended_at=call.get("ended_at"),
    )


@router.get("/calls/{call_id}/transcript", response_model=TranscriptResponse)
async def call_transcript(
    call_id: str,
    silicon: dict = Depends(get_silicon),
) -> TranscriptResponse:
    """Fetch full transcript after the call ends."""
    call = call_store.get(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found.")
    if silicon.get("silicon_id") != call["silicon_id"]:
        raise HTTPException(status_code=403, detail="Not your call.")

    turns = [
        TranscriptTurn(
            speaker=t["speaker"],
            text=t["text"],
            timestamp=t["timestamp"],
        )
        for t in call["turns"]
    ]

    return TranscriptResponse(
        call_id=call_id,
        status=call["status"],
        summary=call["summary"],
        turns=turns,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _livekit_url() -> str:
    """Return the configured LiveKit server URL."""
    from app.config import settings
    return settings.livekit_server_url


async def _optional_silicon(request: Request) -> dict | None:
    key = request.headers.get("X-Silicon-Key")
    if not key:
        return None
    try:
        import httpx
        from app.config import settings

        async with httpx.AsyncClient(base_url=settings.glass_api_url, timeout=10) as client:
            resp = await client.get("/api/v1/silicons/me", headers={"X-Silicon-Key": key})
        if resp.status_code == 200:
            data = resp.json()
            data["_api_key"] = key
            return data
    except Exception:
        pass
    return None
