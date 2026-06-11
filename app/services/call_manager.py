"""Orchestrates the full call lifecycle.

initiate_call():
    1. Resolve Carbon username → Glass ULID
    2. Get/create direct Glass room between Silicon and Carbon
    3. Create LiveKit room
    4. Generate ring_token (HMAC, 60s)
    5. Fetch Carbon's registered devices from Glass
    6. Send VoIP push to all devices
    7. Post m.call_initiated event to Glass room
    8. Spawn Gemini Live bot task
    9. Store call in call_store
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from app import call_store, ring_token
from app.config import settings
from app.services import glass_client, livekit_service, push_service

log = logging.getLogger(__name__)


async def initiate_call(
    carbon_username: str,
    silicon_info: dict,
    message: str = "",
    callback_url: str = "",
) -> dict:
    """Full call setup. Returns the call dict."""

    silicon_id = silicon_info.get("silicon_id", "")
    silicon_display_name = silicon_info.get("name", "Silicon")
    api_key = silicon_info.get("_api_key", "")
    call_id = str(uuid.uuid4())

    # 1. Resolve Carbon handle → full profile
    try:
        carbon = await glass_client.resolve_carbon(carbon_username, api_key)
    except ValueError as exc:
        raise ValueError(str(exc))

    carbon_id = carbon.get("carbon_id") or carbon.get("id", "")
    if not carbon_id:
        raise ValueError(f"Could not determine carbon_id for '{carbon_username}'.")

    # 2. Get or create the direct Glass room
    try:
        glass_room_id = await glass_client.get_or_create_direct_room(
            carbon_id, silicon_id, api_key
        )
    except Exception as exc:
        log.warning("Could not get Glass room (non-fatal): %s", exc)
        glass_room_id = ""

    # 3. Create LiveKit room
    room_name = livekit_service.room_name_for_call(call_id)
    try:
        await livekit_service.create_room(room_name)
    except Exception as exc:
        raise RuntimeError(f"LiveKit room creation failed: {exc}") from exc

    # 4. Create ring_token
    r_token = ring_token.create(call_id)

    # 5. Create call record (before push so bot can start)
    call = call_store.create(
        call_id=call_id,
        silicon_id=silicon_id,
        silicon_display_name=silicon_display_name,
        carbon_username=carbon_username,
        carbon_id=carbon_id,
        glass_room_id=glass_room_id,
        livekit_room=room_name,
        message=message,
        callback_url=callback_url,
    )
    # Stash api_key so glass_client helpers can use it
    call["_silicon_api_key"] = api_key

    # 6. Fetch devices and send VoIP push
    try:
        devices = await glass_client.get_carbon_devices(carbon_id, api_key)
    except Exception as exc:
        log.warning("Could not fetch devices for %s: %s", carbon_username, exc)
        devices = []

    if not devices:
        log.warning("No registered devices for carbon '%s' — push skipped", carbon_username)
    else:
        push_payload = {
            "type": "incoming_call",
            "call_id": call_id,
            "ring_token": r_token,
            "caller_name": silicon_display_name,
            "caller_id": silicon_id,
        }
        push_tasks = [push_service.send_voip_push(device, push_payload) for device in devices]
        await asyncio.gather(*push_tasks, return_exceptions=True)

    # 7. Post m.call_initiated to Glass room
    asyncio.create_task(
        glass_client.post_call_event(call, "m.call_initiated", silicon_api_key=api_key)
    )

    # 8. Spawn Gemini Live bot — it waits for Carbon to answer before joining audio
    from app.services.gemini_bot import run_gemini_bot
    asyncio.create_task(run_gemini_bot(call_id))

    log.info(
        "Call %s initiated: silicon=%s → carbon=%s (room=%s)",
        call_id, silicon_id, carbon_username, room_name,
    )
    return call
