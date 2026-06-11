"""Silicon API-key authentication for silicon-ring.

Validates X-Silicon-Key by calling Glass GET /api/v1/silicons/me.
Returns a dict with silicon info on success; raises 401 on failure.
"""
from __future__ import annotations

import logging

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader

from app.config import settings

log = logging.getLogger(__name__)

_HEADER_SCHEME = APIKeyHeader(name="X-Silicon-Key", auto_error=False)


async def get_silicon(
    request: Request,
    api_key: str | None = Depends(_HEADER_SCHEME),
) -> dict:
    """FastAPI dependency. Returns silicon info dict or raises 401/403."""
    if not api_key:
        raise HTTPException(status_code=401, detail="X-Silicon-Key header required.")

    if not api_key.startswith("scs_live_"):
        raise HTTPException(status_code=401, detail="Invalid API key format.")

    try:
        async with httpx.AsyncClient(base_url=settings.glass_api_url, timeout=10) as client:
            resp = await client.get(
                "/api/v1/silicons/me",
                headers={"X-Silicon-Key": api_key},
            )
    except httpx.RequestError as exc:
        log.error("Glass auth request failed: %s", exc)
        raise HTTPException(status_code=503, detail="Cannot reach Glass to authenticate.")

    if resp.status_code == 401 or resp.status_code == 403:
        raise HTTPException(status_code=401, detail="Invalid or revoked Silicon API key.")

    if resp.status_code != 200:
        log.error("Glass auth returned unexpected status %s", resp.status_code)
        raise HTTPException(status_code=503, detail="Glass authentication error.")

    data = resp.json()
    # Attach the raw key so downstream services can reuse it for Glass calls
    data["_api_key"] = api_key
    return data
