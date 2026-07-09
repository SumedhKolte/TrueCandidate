"""
Recall.ai adapter — the fastest path to REAL Zoom/Meet/Teams meetings.

Recall.ai (https://recall.ai) runs a bot that joins any meeting by URL and
streams real-time transcription + participant events to your webhook. This
router translates Recall's payloads into TrueCandidate's internal contracts, so
the entire signal ensemble runs unchanged on live meetings.

Wiring:
  1. Create a Recall bot with real-time transcription enabled and
     `webhook_url` pointed at  https://<your-host>/webhook/recall
     (local dev: expose uvicorn with `ngrok http 8000`).
  2. Pass your TrueCandidate session id in the bot's `metadata` when creating it:
       {"metadata": {"sherlock_session_id": "<uuid>"}}
  3. Join the meeting from two laptops/phones and start talking.

NOTE: Recall's exact event schema evolves — the parsing below is defensive
(everything via .get) and logs unknown shapes instead of crashing. Check
https://docs.recall.ai for the current payload reference.
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request

from ..config import get_settings
from ..models import PlatformEvent, TranscriptChunk
from ..pipelines import events, transcript

log = logging.getLogger("truecandidate.recall")

router = APIRouter()


# ---------------------------------------------------------------------------
# Bot dispatch: "paste a meet link, the bot joins".
#
# WHY meeting-captions as the default transcript provider: for Google Meet,
# Recall can read the platform's OWN live captions — zero extra ASR cost and
# no provider key. For higher accuracy + word timestamps, switch the provider
# block to e.g. {"deepgram_streaming": {}} or {"assembly_ai_streaming": {}}
# (paid, configured in your Recall dashboard). Speaker attribution does NOT
# depend on the ASR model either way — see the participant-events note below.
# ---------------------------------------------------------------------------
async def create_bot(meeting_url: str, session_id: str) -> dict:
    s = get_settings()
    if not (s.recall_api_key and s.public_base_url):
        raise HTTPException(
            status_code=400,
            detail="RECALL_API_KEY / PUBLIC_BASE_URL not configured — "
                   "set them in backend/.env to enable live meetings.",
        )
    webhook_url = f"{s.public_base_url.rstrip('/')}/webhook/recall"
    body = {
        "meeting_url": meeting_url,
        "bot_name": "TrueCandidate Observer",
        "metadata": {"sherlock_session_id": session_id},
        "recording_config": {
            "transcript": {"provider": {"meeting_captions": {}}},
            "realtime_endpoints": [
                {
                    "type": "webhook",
                    "url": webhook_url,
                    "events": [
                        "transcript.data",
                        "participant_events.join",
                        "participant_events.leave",
                        "participant_events.webcam_on",
                        "participant_events.webcam_off",
                        "participant_events.screenshare_on",
                        "participant_events.screenshare_off",
                    ],
                }
            ],
        },
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{s.recall_api_base}/bot",
            headers={"Authorization": f"Token {s.recall_api_key}"},
            json=body,
        )
    if r.status_code >= 400:
        log.error("recall bot creation failed %s: %s", r.status_code, r.text[:500])
        raise HTTPException(status_code=502,
                            detail=f"Recall.ai rejected the bot: {r.text[:300]}")
    bot = r.json()
    log.info("bot %s dispatched to %s", bot.get("id"), meeting_url)
    return bot


def _session_id(body: dict) -> str | None:
    # We put the TrueCandidate session id in the bot's metadata at creation time.
    return (body.get("data", {}).get("bot_metadata")
            or body.get("data", {}).get("metadata")
            or {}).get("sherlock_session_id")


@router.post("/webhook/recall", status_code=202)
async def recall_webhook(request: Request):
    body = await request.json()
    event = body.get("event", "")
    session_id = _session_id(body)
    if not session_id:
        log.warning("recall event %s without sherlock_session_id metadata", event)
        return {"accepted": False}

    data = body.get("data", {})

    # --- Real-time transcript: one finalized utterance per event -------------
    if event in ("transcript.data", "transcript.partial_data"):
        if event == "transcript.partial_data":
            return {"accepted": True}  # only score finalized utterances
        words = data.get("data", {}).get("words", [])
        speaker = data.get("data", {}).get("participant", {})
        text = " ".join(w.get("text", "") for w in words).strip()
        if text and speaker:
            start = words[0].get("start_timestamp", {}).get("relative", 0)
            end = words[-1].get("end_timestamp", {}).get("relative", start)
            await transcript.handle_chunk(TranscriptChunk(
                session_id=session_id,
                platform_participant_id=str(speaker.get("id", "unknown")),
                display_name=speaker.get("name") or "Unknown",
                text=text,
                started_at_ms=int(start * 1000),
                duration_ms=max(0, int((end - start) * 1000)),
            ))
        return {"accepted": True}

    # --- Participant events ---------------------------------------------------
    mapping = {
        "participant_events.join": "participant_joined",
        "participant_events.leave": "participant_left",
        "participant_events.webcam_on": "webcam_on",
        "participant_events.webcam_off": "webcam_off",
        "participant_events.screenshare_on": "screen_share_started",
        "participant_events.screenshare_off": "screen_share_stopped",
    }
    if event in mapping:
        participant = data.get("data", {}).get("participant", {})
        await events.handle_event(PlatformEvent(
            session_id=session_id,
            platform_participant_id=str(participant.get("id", "unknown")),
            display_name=participant.get("name") or "Unknown",
            event=mapping[event],
        ))
        return {"accepted": True}

    log.info("unhandled recall event: %s", event)
    return {"accepted": True}
