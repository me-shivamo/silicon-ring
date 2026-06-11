"""Gemini Live bot — bidirectional audio bridge inside a LiveKit room.

One asyncio task per active call. Lifecycle:
1. Wait for Carbon to answer (call status → active)
2. Connect to LiveKit room as "silicon-ai"
3. Open Gemini Live streaming session
4. Publish outbound audio track (Gemini → Carbon hears Silicon's voice)
5. Subscribe to Carbon's inbound audio track
6. Two concurrent coroutines:
     carbon_to_gemini: LiveKit AudioFrame → resample 48kHz→16kHz → Gemini input
     gemini_to_carbon: Gemini audio output → LiveKit AudioSource → Carbon speaker
7. Accumulate text transcript from Gemini
8. On call end: generate summary, post m.call_ended to Glass, fire callback

Audio format notes:
  - LiveKit delivers PCM 48kHz 16-bit mono from Carbon's mic
  - Gemini Live expects PCM 16kHz LINEAR16
  - Gemini Live outputs PCM 24kHz LINEAR16
  - We resample inbound 48k→16k with audioop.ratecv (stdlib)
  - Outbound 24k is published directly (LiveKit handles resampling to Carbon's endpoint)
"""
from __future__ import annotations

import asyncio
import audioop
import logging
from datetime import datetime, timezone

from app import call_store
from app.config import settings

log = logging.getLogger(__name__)

# Gemini Live model
_MODEL = "models/gemini-2.0-flash-live-001"

# Audio constants
_CARBON_SAMPLE_RATE = 48_000   # LiveKit default from mic
_GEMINI_IN_RATE = 16_000       # Gemini Live input requirement
_GEMINI_OUT_RATE = 24_000      # Gemini Live output sample rate
_SAMPLE_WIDTH = 2              # 16-bit = 2 bytes per sample
_CHANNELS = 1


async def run_gemini_bot(call_id: str) -> None:
    """Entry point — runs as an asyncio task for the duration of one call."""
    call = call_store.get(call_id)
    if not call:
        return

    log.info("[bot %s] Waiting for Carbon to answer...", call_id)

    # Wait for Carbon to pick up, or time out on missed call
    answer_event: asyncio.Event = call["_answer_event"]
    end_event: asyncio.Event = call["_end_event"]

    done, _ = await asyncio.wait(
        [
            asyncio.ensure_future(answer_event.wait()),
            asyncio.ensure_future(end_event.wait()),
        ],
        return_when=asyncio.FIRST_COMPLETED,
    )

    call = call_store.get(call_id)  # re-fetch after wait
    if not call or call["status"] not in ("active",):
        log.info("[bot %s] Call not answered (status=%s), bot exiting", call_id, call and call["status"])
        return

    log.info("[bot %s] Carbon answered. Starting audio bridge.", call_id)

    try:
        await _run_bridge(call_id, call)
    except Exception as exc:
        log.exception("[bot %s] Bridge crashed: %s", call_id, exc)
        call_store.mark_ended(call_id, status="failed")
    finally:
        await _finalize(call_id)


async def _run_bridge(call_id: str, call: dict) -> None:
    """Connect to LiveKit and Gemini Live, bridge audio until call ends."""
    from livekit import rtc
    from google import genai

    room_name = call["livekit_room"]
    message = call.get("message", "")
    end_event: asyncio.Event = call["_end_event"]

    # Queue for Carbon's raw PCM frames (bytes)
    carbon_audio_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
    # Resampler state for audioop.ratecv (48k → 16k)
    _ratecv_state = None

    # --- LiveKit room ---
    lk_room = rtc.Room()

    @lk_room.on("track_subscribed")
    def on_track_subscribed(track, publication, participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            log.info("[bot %s] Subscribed to Carbon audio track", call_id)
            audio_stream = rtc.AudioStream(track)

            async def _drain_stream():
                nonlocal _ratecv_state
                async for event in audio_stream:
                    if call_store.get(call_id) is None or end_event.is_set():
                        break
                    frame: rtc.AudioFrame = event.frame
                    pcm_bytes = bytes(frame.data)
                    # Resample 48kHz → 16kHz (3:1 ratio)
                    resampled, _ratecv_state = audioop.ratecv(
                        pcm_bytes,
                        _SAMPLE_WIDTH,
                        _CHANNELS,
                        _CARBON_SAMPLE_RATE,
                        _GEMINI_IN_RATE,
                        _ratecv_state,
                    )
                    try:
                        carbon_audio_q.put_nowait(resampled)
                    except asyncio.QueueFull:
                        pass  # Drop oldest in heavy load

            asyncio.ensure_future(_drain_stream())

    bot_token = _make_bot_token(room_name)
    await lk_room.connect(settings.livekit_server_url, bot_token)
    log.info("[bot %s] Connected to LiveKit room %s", call_id, room_name)

    # Publish outbound audio source (Gemini → Carbon)
    audio_source = rtc.AudioSource(sample_rate=_GEMINI_OUT_RATE, num_channels=_CHANNELS)
    out_track = rtc.LocalAudioTrack.create_audio_track("silicon-voice", audio_source)
    pub_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    await lk_room.local_participant.publish_track(out_track, pub_options)

    # --- Gemini Live session ---
    system_prompt = _build_system_prompt(call, message)
    genai_client = genai.Client(api_key=settings.gemini_api_key)

    live_config = {
        "response_modalities": ["AUDIO"],
        "system_instruction": system_prompt,
        "speech_config": {
            "voice_config": {
                "prebuilt_voice_config": {"voice_name": "Aoede"}
            }
        },
        "input_audio_transcription": {},   # enable transcript on input
        "output_audio_transcription": {},  # enable transcript on output
    }

    async with genai_client.aio.live.connect(model=_MODEL, config=live_config) as session:
        log.info("[bot %s] Gemini Live session open", call_id)

        async def carbon_to_gemini():
            """Pull PCM from queue and stream to Gemini."""
            while not end_event.is_set():
                try:
                    pcm = await asyncio.wait_for(carbon_audio_q.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                try:
                    await session.send(
                        input={"data": pcm, "mime_type": f"audio/pcm;rate={_GEMINI_IN_RATE}"},
                        end_of_turn=False,
                    )
                except Exception as exc:
                    log.warning("[bot %s] Gemini send error: %s", call_id, exc)
                    break

        async def gemini_to_carbon():
            """Receive Gemini output and publish to LiveKit."""
            async for response in session.receive():
                if end_event.is_set():
                    break

                # Audio output → publish to LiveKit room
                if hasattr(response, "data") and response.data:
                    pcm = response.data
                    samples_per_channel = len(pcm) // _SAMPLE_WIDTH
                    frame = rtc.AudioFrame(
                        data=pcm,
                        sample_rate=_GEMINI_OUT_RATE,
                        num_channels=_CHANNELS,
                        samples_per_channel=samples_per_channel,
                    )
                    try:
                        await audio_source.capture_frame(frame)
                    except Exception as exc:
                        log.warning("[bot %s] LiveKit publish error: %s", call_id, exc)

                # Transcript deltas
                if hasattr(response, "server_content") and response.server_content:
                    sc = response.server_content
                    # Output transcript (silicon speaking)
                    if hasattr(sc, "output_transcription") and sc.output_transcription:
                        text = sc.output_transcription.text or ""
                        if text:
                            call_store.append_turn(call_id, "silicon", text)
                    # Input transcript (carbon speaking)
                    if hasattr(sc, "input_transcription") and sc.input_transcription:
                        text = sc.input_transcription.text or ""
                        if text:
                            call_store.append_turn(call_id, "carbon", text)

        await asyncio.gather(
            carbon_to_gemini(),
            gemini_to_carbon(),
            _wait_for_end(end_event),
        )

    await lk_room.disconnect()
    log.info("[bot %s] LiveKit disconnected, Gemini session closed", call_id)


async def _wait_for_end(end_event: asyncio.Event) -> None:
    await end_event.wait()


async def _finalize(call_id: str) -> None:
    """Post-call cleanup: generate summary, post Glass event, fire callback."""
    call = call_store.get(call_id)
    if not call:
        return

    # Mark ended if not already (e.g. bridge crashed)
    if call["status"] not in ("ended", "missed", "failed"):
        call_store.mark_ended(call_id, status="ended")
    call = call_store.get(call_id)

    # Generate summary from transcript
    summary = _summarize_turns(call["turns"])
    call_store.set_summary(call_id, summary)

    # Post m.call_ended to Glass
    from app.services.glass_client import post_call_event
    await post_call_event(
        call,
        "m.call_ended",
        extra_content={
            "outcome": call["status"],
            "summary": summary,
            "turn_count": len(call["turns"]),
        },
        silicon_api_key=call.get("_silicon_api_key", ""),
    )

    # Fire callback if provided
    callback_url = call.get("callback_url", "")
    if callback_url:
        await _fire_callback(call, callback_url)

    log.info("[bot %s] Finalized. status=%s turns=%d", call_id, call["status"], len(call["turns"]))


def _summarize_turns(turns: list[dict]) -> str:
    if not turns:
        return ""
    # Simple extractive summary — first silicon turn as summary
    silicon_lines = [t["text"] for t in turns if t["speaker"] == "silicon"]
    if silicon_lines:
        first = silicon_lines[0][:200]
        return first if len(silicon_lines) == 1 else f"{first}... ({len(turns)} total turns)"
    return f"Call had {len(turns)} turns."


async def _fire_callback(call: dict, callback_url: str) -> None:
    import httpx

    payload = {
        "call_id": call["call_id"],
        "status": call["status"],
        "summary": call["summary"],
        "turns": [
            {
                "speaker": t["speaker"],
                "text": t["text"],
                "timestamp": t["timestamp"].isoformat(),
            }
            for t in call["turns"]
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(callback_url, json=payload)
        log.info("[bot %s] Callback → %s HTTP %s", call["call_id"], callback_url, resp.status_code)
    except Exception as exc:
        log.warning("[bot %s] Callback failed: %s", call["call_id"], exc)


def _build_system_prompt(call: dict, message: str) -> str:
    silicon_name = call.get("silicon_display_name", "Silicon")
    carbon_name = call.get("carbon_username", "the user")
    base = (
        f"You are {silicon_name}, an AI assistant, on a live voice call with {carbon_name}. "
        "Speak naturally and concisely. This is a real-time phone call — keep responses short. "
        "Listen carefully and respond directly to what the user says."
    )
    if message:
        base += f"\n\nContext for this call: {message}"
    return base


def _make_bot_token(room_name: str) -> str:
    from app.services.livekit_service import create_bot_token
    return create_bot_token(room_name)
