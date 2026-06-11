"""Short-lived HMAC tokens embedded in VoIP push payloads.

The mobile app receives the ring_token in the push payload and presents it
to /calls/{id}/answer or /calls/{id}/hangup to authenticate without a
full Silicon API key.

Format: <call_id>.<expires_unix>.<hmac_hex>
TTL: 60 seconds after call initiation (configurable).
"""
from __future__ import annotations

import hashlib
import hmac
import time

from app.config import settings

_TTL = 60  # seconds


def _sign(call_id: str, expires: int) -> str:
    msg = f"{call_id}.{expires}".encode()
    return hmac.new(settings.ring_hmac_secret.encode(), msg, hashlib.sha256).hexdigest()


def create(call_id: str) -> str:
    expires = int(time.time()) + _TTL
    sig = _sign(call_id, expires)
    return f"{call_id}.{expires}.{sig}"


def verify(token: str) -> str | None:
    """Return call_id if token is valid and unexpired, else None."""
    try:
        call_id, expires_str, sig = token.rsplit(".", 2)
        expires = int(expires_str)
    except (ValueError, AttributeError):
        return None

    if int(time.time()) > expires:
        return None

    expected = _sign(call_id, expires)
    if not hmac.compare_digest(expected, sig):
        return None

    return call_id
