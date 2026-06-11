"""VoIP push notification dispatch.

Android: FCM via firebase-admin (data-only message so app wakes in background).
iOS:     APNs VoIP push via PushKit (HTTP/2 + JWT, httpx).

Both payloads carry:
    type         = "incoming_call"
    call_id      = <uuid>
    ring_token   = <HMAC-signed 60s token>
    caller_name  = Silicon display name
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FCM (Android)
# ---------------------------------------------------------------------------

_fcm_app = None


def _get_fcm_app():
    global _fcm_app
    if _fcm_app is not None:
        return _fcm_app
    if not settings.firebase_credentials_json:
        raise RuntimeError("FIREBASE_CREDENTIALS_JSON not configured.")
    import firebase_admin
    from firebase_admin import credentials

    cred = credentials.Certificate(settings.firebase_credentials_json)
    _fcm_app = firebase_admin.initialize_app(cred)
    return _fcm_app


async def send_fcm_voip_push(token: str, payload: dict[str, Any]) -> None:
    """Send a data-only FCM message. Raises on failure."""
    import asyncio

    from firebase_admin import messaging

    _get_fcm_app()

    # All values in FCM data messages must be strings
    data = {k: str(v) for k, v in payload.items()}

    message = messaging.Message(
        data=data,
        token=token,
        android=messaging.AndroidConfig(priority="high"),
    )

    # firebase_admin is synchronous; run in executor
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, messaging.send, message)
    log.info("FCM push sent to token ...%s", token[-8:])


# ---------------------------------------------------------------------------
# APNs (iOS) — HTTP/2 via httpx
# ---------------------------------------------------------------------------

_apns_jwt_cache: dict[str, Any] = {}  # {"token": str, "issued_at": float}
_JWT_TTL = 45 * 60  # 45 min (APNs JWTs expire in 1 h)


def _make_apns_jwt() -> str:
    """Generate a signed APNs provider JWT. Cached for 45 minutes."""
    import jwt  # PyJWT

    now = time.time()
    cached = _apns_jwt_cache.get("token")
    if cached and (now - _apns_jwt_cache.get("issued_at", 0)) < _JWT_TTL:
        return cached

    if not settings.apns_key_file:
        raise RuntimeError("APNS_KEY_FILE not configured.")

    with open(settings.apns_key_file) as f:
        private_key = f.read()

    token = jwt.encode(
        {"iss": settings.apns_team_id, "iat": int(now)},
        private_key,
        algorithm="ES256",
        headers={"kid": settings.apns_key_id},
    )
    _apns_jwt_cache["token"] = token
    _apns_jwt_cache["issued_at"] = now
    return token


async def send_apns_voip_push(token: str, payload: dict[str, Any]) -> None:
    """Send a VoIP push via APNs HTTP/2. Raises on failure."""
    host = (
        "api.sandbox.push.apple.com" if settings.apns_use_sandbox else "api.push.apple.com"
    )
    url = f"https://{host}/3/device/{token}"

    headers = {
        "authorization": f"bearer {_make_apns_jwt()}",
        "apns-topic": f"{settings.apns_topic}.voip",
        "apns-push-type": "voip",
        "apns-priority": "10",
    }

    async with httpx.AsyncClient(http2=True, timeout=10) as client:
        resp = await client.post(url, headers=headers, content=json.dumps(payload))

    if resp.status_code != 200:
        raise RuntimeError(f"APNs returned HTTP {resp.status_code}: {resp.text[:200]}")

    log.info("APNs VoIP push sent to token ...%s", token[-8:])


# ---------------------------------------------------------------------------
# Dispatch (platform-aware)
# ---------------------------------------------------------------------------

async def send_voip_push(device: dict[str, Any], payload: dict[str, Any]) -> None:
    """Route to FCM or APNs depending on device platform. Logs but does not raise."""
    platform = device.get("platform", "")
    token = device.get("token", "")
    if not token:
        log.warning("push: no token for device %s", device)
        return

    try:
        if platform == "android":
            await send_fcm_voip_push(token, payload)
        elif platform == "ios":
            await send_apns_voip_push(token, payload)
        else:
            log.warning("push: unknown platform '%s', skipping", platform)
    except Exception as exc:
        log.error("VoIP push failed for platform=%s token=...%s: %s", platform, token[-8:], exc)
