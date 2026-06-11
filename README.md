# silicon-ring

Central voice call routing server — Silicon rings Carbon.

A Silicon AI agent runs `silicon-ring call shivam` from its terminal. The Carbon's Glass app (Android/iOS/web) rings like a WhatsApp call. Carbon picks up and has a live AI voice conversation powered by **Gemini Live** over **LiveKit**.

---

## How it works

```
Silicon agent terminal
  $ silicon-ring call shivam
         │
         │ POST /api/v1/calls/initiate  (X-Silicon-Key)
         ▼
silicon-ring server  (FastAPI, port 8010)
         │
  ┌──────┼──────────────────────────────┐
  │      │                              │
  │   Glass API                    LiveKit
  │   resolve carbon username      create room
  │   fetch push tokens            generate tokens
  │      │                              │
  │      ▼                              │
  │   VoIP push                         │
  │   FCM (Android)                     │
  │   APNs (iOS)                        │
  │      │                              │
  │      ▼                              │
  │   Carbon's Glass app                │
  │   native call screen                │
  │      │  (user picks up)             │
  │      └──────── LiveKit room ────────┘
  │                     │
  │             Gemini Live bot
  │             Carbon audio → Gemini → Silicon voice
  │             Transcript accumulated
  │
  └── call ends → transcript returned to Silicon terminal
```

**Key properties:**
- Stateless — no database. Active call state is in memory; push tokens live in Glass.
- One asyncio task per call. Multiple calls run concurrently.
- Silicon authenticates using its existing Glass `scs_live_` API key.
- Carbon authenticates incoming-call actions with a HMAC-signed `ring_token` (60s TTL) embedded in the push payload.
- Call history (`m.call_initiated`, `m.call_ended` events) is written into the Silicon↔Carbon Glass room.

---

## Prerequisites

| Service | Purpose |
|---------|---------|
| **Glass** | Carbon/Silicon registry, push token storage, chat history |
| **LiveKit** | Real-time audio routing (self-hosted or cloud.livekit.io) |
| **Gemini Live API** | AI voice — bidirectional audio streaming |
| **Firebase** | FCM push for Android |
| **Apple APNs** | VoIP push for iOS (PushKit) |

---

## Installation

### Server

```bash
git clone <this repo>
cd silicon-ring

# Create and activate a virtualenv
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env — fill in GLASS_API_URL, LIVEKIT_*, GEMINI_API_KEY,
#             FIREBASE_CREDENTIALS_JSON, APNS_* keys, RING_HMAC_SECRET
```

### CLI (on each Silicon agent VPS)

```bash
pip install -e /path/to/silicon-ring   # or pip install silicon-ring when published

# One-time config
silicon-ring config set endpoint https://ring.yourdomain.com
silicon-ring config set api-key scs_live_...

# Verify
silicon-ring config show
```

Config is stored in `~/.silicon-ring/config.toml`. Environment variable overrides: `SILICON_RING_ENDPOINT`, `SILICON_RING_API_KEY`.

---

## Running the server

```bash
# Development
uvicorn main:app --host 0.0.0.0 --port 8010 --reload

# Production (single worker — asyncio tasks handle concurrency)
uvicorn main:app --host 127.0.0.1 --port 8010 --workers 1
```

Interactive API docs available at `http://localhost:8010/docs`.

### systemd (production)

Copy `silicon-ring.service` to `/etc/systemd/system/`, update paths, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable silicon-ring
sudo systemctl start silicon-ring
sudo journalctl -u silicon-ring -f
```

---

## CLI usage

```bash
# Call a Carbon (blocks until call ends, then prints transcript)
silicon-ring call shivam
silicon-ring call shivam --message "want to discuss the deploy plan"

# Call without waiting (returns call_id immediately)
silicon-ring call shivam --async

# Check call status
silicon-ring status <call_id>

# Print transcript after call ends
silicon-ring transcript <call_id>
silicon-ring transcript <call_id> --json   # raw JSON output
```

**Synchronous mode (default):** The CLI polls until the call ends, then prints the full transcript to stdout. This is the natural mode for Silicon agents — a terminal worker runs the command and gets the conversation back as output, just like any other shell command.

**Async mode (`--async`):** Returns immediately with the `call_id`. Use `silicon-ring status` and `silicon-ring transcript` to follow up.

---

## REST API

Base URL: `http://your-server:8010`

All Silicon-facing endpoints require `X-Silicon-Key: scs_live_...` (the Silicon's Glass API key).
Mobile-facing endpoints use the `ring_token` from the push payload.

### `GET /health`
Liveness check. No auth.
```json
{"status": "ok", "service": "silicon-ring"}
```

### `POST /api/v1/calls/initiate`
Silicon initiates a call to a Carbon.

**Auth:** `X-Silicon-Key`

**Request:**
```json
{
  "carbon_username": "shivam",
  "message": "Optional context passed to Gemini as system prompt seed"
}
```

**Response 202:**
```json
{"call_id": "550e8400-...", "status": "ringing"}
```

**Response 404:** Carbon not found in Glass.
**Response 502:** LiveKit room creation failed.

---

### `POST /api/v1/calls/{call_id}/answer`
Carbon accepted the call — exchange `ring_token` for LiveKit credentials.

**Auth:** `ring_token` from push payload (HMAC-signed, 60s TTL).

**Request:**
```json
{"ring_token": "<from push payload>"}
```

**Response 200:**
```json
{
  "call_id": "550e8400-...",
  "livekit_url": "wss://livekit.yourdomain.com",
  "livekit_token": "<LiveKit participant JWT>",
  "silicon_display_name": "Silicon"
}
```

Connect to the LiveKit room using the official LiveKit SDK with these credentials. The AI bot is already in the room.

---

### `POST /api/v1/calls/{call_id}/hangup`
Either party ends the call.

**Auth:** `ring_token` (Carbon) **or** `X-Silicon-Key` (Silicon).

**Request:**
```json
{"ring_token": "<from push payload>"}
```
Omit `ring_token` when authenticating as Silicon.

**Response 200:** `{"ended": true}`

---

### `GET /api/v1/calls/{call_id}`
Poll call status.

**Auth:** `X-Silicon-Key`

**Response 200:**
```json
{
  "call_id": "...",
  "status": "ringing | active | ended | missed | failed",
  "silicon_id": "...",
  "carbon_username": "shivam",
  "initiated_at": "2026-06-11T10:00:00Z",
  "answered_at": "2026-06-11T10:00:08Z",
  "ended_at": "2026-06-11T10:05:23Z"
}
```

---

### `GET /api/v1/calls/{call_id}/transcript`
Fetch full transcript after call ends.

**Auth:** `X-Silicon-Key`

**Response 200:**
```json
{
  "call_id": "...",
  "status": "ended",
  "summary": "Silicon discussed the deploy plan. Carbon approved merging to main.",
  "turns": [
    {"speaker": "silicon", "text": "Hey, wanted to talk about the deploy.", "timestamp": "..."},
    {"speaker": "carbon",  "text": "Sure, what's up?", "timestamp": "..."}
  ]
}
```

---

## Carbon app integration (mobile team)

### 1 — Register device push token
Call this on every app launch after login:

```
POST /api/v1/devices/register/   (on Glass, not silicon-ring)
Authorization: Bearer <Glass JWT>
Content-Type: application/json

{
  "platform": "ios",          // or "android" or "web"
  "token": "<APNs VoIP token or FCM registration token>",
  "app_bundle": "com.example.glass"
}
```

### 2 — Receive VoIP push

**Android** — FCM data-only payload (no `notification` key — ensures background wakeup):
```json
{
  "type": "incoming_call",
  "call_id": "550e8400-...",
  "ring_token": "<60s HMAC token>",
  "caller_name": "Silicon",
  "caller_id": "ada-silicon"
}
```
Present a call screen using `ConnectionService` + `TelecomManager.addNewIncomingCall()`.

**iOS** — APNs VoIP push via PushKit (`PKPushType.voIP`):
```json
{
  "type": "incoming_call",
  "call_id": "550e8400-...",
  "ring_token": "<60s HMAC token>",
  "caller_name": "Silicon",
  "caller_id": "ada-silicon"
}
```
Present the native call screen via `CXProvider.reportNewIncomingCall(with:update:)` inside `PKPushRegistryDelegate.pushRegistry(_:didReceiveIncomingPushWith:)`.

### 3 — Answer: get LiveKit credentials
```
POST https://ring.yourdomain.com/api/v1/calls/{call_id}/answer
Content-Type: application/json

{"ring_token": "<from push payload>"}
```
Use the returned `livekit_url` and `livekit_token` with the LiveKit SDK:
- **iOS:** [LiveKit Swift SDK](https://github.com/livekit/client-sdk-swift) via SPM
- **Android:** `io.livekit:livekit-android` via Gradle

Enable the microphone. The Gemini AI bot is already in the room and will start speaking.

### 4 — Hangup
```
POST https://ring.yourdomain.com/api/v1/calls/{call_id}/hangup
Content-Type: application/json

{"ring_token": "<from push payload>"}
```
Also disconnect from the LiveKit room locally via the SDK.

---

## Glass changes (already included in this repo)

Two small additions were made to the Glass backend:

**New model — `CarbonDevice`** (`apps/accounts/models.py`):
Stores Carbon push tokens. Migrations: `0023_carbondevice.py`.

**New endpoints** (`apps/accounts/urls.py`):
- `POST /api/v1/devices/register/` — Carbon registers push token (JWT auth)
- `GET /api/v1/carbons/{carbon_id}/devices/` — silicon-ring fetches tokens (Silicon key auth)

**New event types** (`apps/chat/models.py`):
- `m.call_initiated` — posted when silicon-ring starts a call
- `m.call_ended` — posted when the call ends (with outcome + summary)

Run the Glass migrations after pulling these changes:
```bash
python manage.py migrate
```

---

## Configuration reference (`.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `GLASS_API_URL` | Yes | Glass backend URL, e.g. `http://127.0.0.1:8000` |
| `LIVEKIT_API_KEY` | Yes | LiveKit API key |
| `LIVEKIT_API_SECRET` | Yes | LiveKit API secret |
| `LIVEKIT_SERVER_URL` | Yes | LiveKit server WebSocket URL, e.g. `wss://livekit.example.com` |
| `GEMINI_API_KEY` | Yes | Google Gemini API key (needs access to Gemini Live) |
| `FIREBASE_CREDENTIALS_JSON` | Android | Path to Firebase service account JSON |
| `APNS_KEY_FILE` | iOS | Path to `.p8` APNs auth key |
| `APNS_KEY_ID` | iOS | APNs key ID (10-char string) |
| `APNS_TEAM_ID` | iOS | Apple Developer Team ID |
| `APNS_TOPIC` | iOS | App bundle ID, e.g. `com.example.glass` |
| `APNS_USE_SANDBOX` | iOS | `true` for development builds (default `false`) |
| `RING_HMAC_SECRET` | Yes | Random secret for signing ring tokens — **change in prod** |
| `RING_TIMEOUT_SECONDS` | No | Seconds before unanswered call becomes missed (default `60`) |
| `PORT` | No | Server port (default `8010`) |

---

## Project structure

```
silicon-ring/
├── main.py                      # FastAPI app entry point
├── setup.py                     # pip-installable CLI package
├── requirements.txt
├── .env.example
├── silicon-ring.service         # systemd unit
│
├── app/
│   ├── config.py                # pydantic-settings
│   ├── schemas.py               # Pydantic request/response models
│   ├── call_store.py            # In-memory call state
│   ├── ring_token.py            # HMAC ring_token sign/verify
│   │
│   ├── auth/
│   │   └── silicon.py           # FastAPI dep: validate scs_live_ key via Glass
│   │
│   ├── routers/
│   │   └── calls.py             # All call endpoints
│   │
│   └── services/
│       ├── glass_client.py      # Glass API: resolve carbon, get devices, post events
│       ├── livekit_service.py   # LiveKit room + token management
│       ├── push_service.py      # FCM + APNs VoIP push dispatch
│       ├── gemini_bot.py        # Gemini Live ↔ LiveKit audio bridge
│       └── call_manager.py      # Full call lifecycle orchestration
│
└── ring_cli/
    └── cli.py                   # `silicon-ring` CLI (click)
```

---

## Audio pipeline

```
Carbon mic (48kHz PCM)
    │
    │  LiveKit SDK (Carbon's device)
    ▼
LiveKit room
    │
    │  livekit.rtc AudioStream
    ▼
silicon-ring bot (audioop.ratecv)
    │ resample 48kHz → 16kHz
    ▼
Gemini Live session
    │ session.send(pcm, mime="audio/pcm;rate=16000")
    │
    │ (Gemini processes speech, generates response)
    │
    │ session.receive() → PCM 24kHz
    ▼
silicon-ring bot
    │
    │  livekit.rtc AudioSource → LocalAudioTrack
    ▼
LiveKit room
    │
    │  LiveKit SDK (Carbon's device)
    ▼
Carbon speaker
```

Gemini Live also returns text transcription of both sides, which silicon-ring accumulates and returns as the call transcript.
