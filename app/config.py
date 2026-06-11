"""All configuration via environment variables, with sensible defaults for dev."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Glass backend
    glass_api_url: str = "http://127.0.0.1:8000"

    # LiveKit
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    livekit_server_url: str = "wss://localhost:7880"

    # Gemini
    gemini_api_key: str = ""

    # FCM — path to Firebase service account JSON
    firebase_credentials_json: str = ""

    # APNs
    apns_key_file: str = ""      # path to .p8 file
    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_topic: str = ""         # iOS bundle ID, e.g. com.example.glass
    apns_use_sandbox: bool = False

    # HMAC secret for ring_tokens embedded in push payloads
    ring_hmac_secret: str = "dev-secret-change-in-prod"

    # Misc
    port: int = 8010
    # How long (seconds) a ringing call waits before auto-marking missed
    ring_timeout_seconds: int = 60


settings = Settings()
