"""LiveKit room management and participant token generation."""
from __future__ import annotations

import logging
import time

from livekit.api import AccessToken, VideoGrants, LiveKitAPI, CreateRoomRequest

from app.config import settings

log = logging.getLogger(__name__)

_TOKEN_TTL = 3 * 60 * 60  # 3 hours


async def create_room(room_name: str) -> None:
    """Create a LiveKit room. Idempotent — LiveKit returns existing room if name exists."""
    async with LiveKitAPI(
        url=settings.livekit_server_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    ) as api:
        room = await api.room.create_room(CreateRoomRequest(name=room_name))
        log.info("LiveKit room ready: %s (sid=%s)", room.name, room.sid)


def create_carbon_token(room_name: str, carbon_username: str) -> str:
    """Generate a LiveKit participant token for the Carbon (human)."""
    token = (
        AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(f"carbon-{carbon_username}")
        .with_name(carbon_username)
        .with_ttl(seconds=_TOKEN_TTL)
        .with_grants(
            VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
            )
        )
    )
    return token.to_jwt()


def create_bot_token(room_name: str) -> str:
    """Generate a LiveKit participant token for the Gemini bot (silicon-ai)."""
    token = (
        AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity("silicon-ai")
        .with_name("Silicon")
        .with_ttl(seconds=_TOKEN_TTL)
        .with_grants(
            VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
            )
        )
    )
    return token.to_jwt()


def room_name_for_call(call_id: str) -> str:
    return f"call-{call_id}"
