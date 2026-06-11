"""silicon-ring — Central voice call routing server.

Start with: uvicorn main:app --host 0.0.0.0 --port 8010
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import calls

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("silicon-ring")

app = FastAPI(
    title="silicon-ring",
    description="Central voice call routing server — Silicon rings Carbon.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(calls.router, prefix="/api/v1")


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "service": "silicon-ring"}


@app.on_event("startup")
async def startup() -> None:
    log.info("silicon-ring started on port %s", settings.port)
    log.info("Glass API: %s", settings.glass_api_url)
    log.info("LiveKit:   %s", settings.livekit_server_url)
    # Start background task that auto-marks calls as missed after ring_timeout_seconds
    asyncio.create_task(_ring_watchdog())


async def _ring_watchdog() -> None:
    """Every 5 s scan for ringing calls that have exceeded the ring timeout."""
    from app import call_store

    while True:
        await asyncio.sleep(5)
        try:
            import datetime

            now = datetime.datetime.now(datetime.timezone.utc)
            for call in call_store.all_calls():
                if call["status"] != "ringing":
                    continue
                age = (now - call["initiated_at"]).total_seconds()
                if age >= settings.ring_timeout_seconds:
                    log.info("Call %s timed out (missed)", call["call_id"])
                    call_store.mark_ended(call["call_id"], status="missed")
                    # Post missed event to Glass and fire callback in background
                    asyncio.create_task(_finalize_missed(call["call_id"]))
        except Exception as exc:
            log.exception("ring_watchdog error: %s", exc)


async def _finalize_missed(call_id: str) -> None:
    from app import call_store
    from app.services.glass_client import post_call_event

    call = call_store.get(call_id)
    if not call:
        return
    try:
        await post_call_event(call, "m.call_ended", {"outcome": "missed"})
    except Exception as exc:
        log.warning("Failed to post missed call event to Glass: %s", exc)
