"""HTTP client for the Glass backend.

Handles:
- Carbon handle → ULID resolution
- Carbon device push token lookup
- Getting/creating the direct room between a Silicon and Carbon
- Posting m.call_* events into that room
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_TIMEOUT = 10  # seconds


def _headers(silicon_api_key: str) -> dict[str, str]:
    return {"X-Silicon-Key": silicon_api_key}


async def resolve_carbon(username: str, silicon_api_key: str) -> dict[str, Any]:
    """Resolve a Carbon username (or carbon_id) to its full profile.

    Uses Glass GET /api/v1/handle/carbon/{handle}.
    Returns the carbon dict or raises ValueError if not found.
    """
    async with httpx.AsyncClient(base_url=settings.glass_api_url, timeout=_TIMEOUT) as client:
        resp = await client.get(
            f"/api/v1/handle/carbon/{username}",
            headers=_headers(silicon_api_key),
        )

    if resp.status_code == 404:
        raise ValueError(f"Carbon '{username}' not found in Glass.")
    if resp.status_code != 200:
        raise RuntimeError(f"Glass handle lookup failed: HTTP {resp.status_code}")

    return resp.json()


async def get_carbon_devices(carbon_id: str, silicon_api_key: str) -> list[dict[str, Any]]:
    """Fetch registered push tokens for a Carbon.

    Uses Glass GET /api/v1/carbons/{carbon_id}/devices/
    Returns a list of device dicts: [{platform, token, app_bundle}, ...]
    Raises RuntimeError if the request fails.
    """
    async with httpx.AsyncClient(base_url=settings.glass_api_url, timeout=_TIMEOUT) as client:
        resp = await client.get(
            f"/api/v1/carbons/{carbon_id}/devices/",
            headers=_headers(silicon_api_key),
        )

    if resp.status_code == 404:
        return []
    if resp.status_code != 200:
        raise RuntimeError(f"Glass device lookup failed: HTTP {resp.status_code}")

    return resp.json()


async def get_or_create_direct_room(
    carbon_id: str, silicon_id: str, silicon_api_key: str
) -> str:
    """Get or create the direct room between this Silicon and the Carbon.

    Returns the Glass room_id (ULID string).
    Uses POST /api/v1/rooms/direct {target_kind: "carbon", target_id: carbon_id}.
    """
    async with httpx.AsyncClient(base_url=settings.glass_api_url, timeout=_TIMEOUT) as client:
        resp = await client.post(
            "/api/v1/rooms/direct",
            json={"target_kind": "carbon", "target_id": carbon_id},
            headers=_headers(silicon_api_key),
        )

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Glass room create failed: HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    return data["room_id"]


async def post_call_event(
    call: dict[str, Any],
    event_type: str,
    extra_content: dict[str, Any] | None = None,
    silicon_api_key: str = "",
) -> None:
    """Post a call lifecycle event (m.call_initiated / m.call_ended) into the Glass room.

    Safe to call and ignore errors — Glass history is nice-to-have, not critical path.
    """
    key = silicon_api_key or call.get("_silicon_api_key", "")
    room_id = call.get("glass_room_id", "")
    if not room_id or not key:
        log.warning("post_call_event: missing room_id or api_key, skipping")
        return

    content: dict[str, Any] = {
        "call_id": call["call_id"],
        "silicon_id": call["silicon_id"],
        "carbon_username": call["carbon_username"],
    }
    if extra_content:
        content.update(extra_content)

    try:
        async with httpx.AsyncClient(base_url=settings.glass_api_url, timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"/api/v1/rooms/{room_id}/events",
                json={"type": event_type, "content": content},
                headers=_headers(key),
            )
        if resp.status_code not in (200, 201):
            log.warning("post_call_event %s → HTTP %s", event_type, resp.status_code)
    except Exception as exc:
        log.warning("post_call_event failed (non-critical): %s", exc)
