"""
TrueCandidate backend — FastAPI entrypoint.

Architecture in one paragraph: this service is a thin, stateless-ish signal
emitter. Webhooks arrive from platform bot adapters (Zoom/Meet/Teams),
pipelines extract WEAK SIGNALS (some deterministic, some via Groq), and every
signal is appended to Supabase. A Postgres trigger owns the scoring math and
Supabase Realtime owns fan-out to dashboards. That split is why this scales:
add more Render instances for ingest throughput; Postgres serializes scoring;
no websocket fan-out code to maintain ourselves.

Latency: webhook handlers ACK immediately (202) and run the pipeline as an
asyncio background task. The platform adapter is never blocked by Groq, and
per-chunk processing stays under the 500ms budget via groq_client's deadline.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .adapters import recall
from .config import get_settings
from .models import LaunchRequest, PlatformEvent, TranscriptChunk
from .pipelines import events, passive, probing, transcript

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("truecandidate")

_background: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    """Track fire-and-forget tasks so exceptions are logged, not swallowed."""
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    task.add_done_callback(
        lambda t: t.cancelled() or t.exception() is None
        or log.error("pipeline task failed", exc_info=t.exception())
    )


async def _sweeper() -> None:
    """Periodic heuristics: passive-observer filter + ambiguity/probing."""
    interval = get_settings().sweep_interval_s
    while True:
        try:
            await passive.sweep()
            await probing.sweep()
        except Exception:
            log.exception("sweep failed")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    sweeper = asyncio.create_task(_sweeper())
    yield
    sweeper.cancel()
    await db.close()


app = FastAPI(title="TrueCandidate — Candidate Identification Engine",
              lifespan=lifespan)

# Dashboard is on Vercel, API on Render — CORS is required. Lock `origins`
# down to the real dashboard domain in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Real-meeting ingestion (Recall.ai bot -> /webhook/recall)
app.include_router(recall.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook/transcript", status_code=202)
async def webhook_transcript(chunk: TranscriptChunk):
    """Diarized transcript chunks from the meeting bot."""
    _spawn(transcript.handle_chunk(chunk))
    return {"accepted": True}


@app.post("/webhook/events", status_code=202)
async def webhook_events(event: PlatformEvent):
    """Platform events: joins, webcam toggles, screen shares, lip samples."""
    _spawn(events.handle_event(event))
    return {"accepted": True}


def _platform_from_url(url: str) -> str:
    if "meet.google" in url:
        return "meet"
    if "zoom" in url:
        return "zoom"
    if "teams" in url:
        return "teams"
    raise ValueError("Unsupported meeting URL — expected Meet, Zoom, or Teams")


@app.post("/sessions/launch")
async def launch_session(req: LaunchRequest):
    """The real-life entry point: paste a meeting link + expected candidates.
    TrueCandidate creates the session, then sends a bot into the call:
      * Recall.ai bot when RECALL_API_KEY is configured (Meet/Zoom/Teams), or
      * the built-in self-hosted Playwright bot (Google Meet, zero third
        parties) when it isn't — see bot/meet_bot.py.
    """
    try:
        platform = _platform_from_url(req.meeting_url)
    except ValueError as e:
        return {"error": str(e)}

    session = await db.create_session(
        platform=platform,
        candidates=[c.strip() for c in req.candidates if c.strip()],
        interviewers=[i.strip() for i in req.interviewers if i.strip()],
    )

    # Chrome-extension mode: the interviewer's own tab captures captions, so
    # nothing joins the call. Just hand back the session id.
    if not req.dispatch_bot:
        return {"session_id": session["id"], "platform": platform,
                "bot": "extension"}

    if get_settings().recall_api_key:
        bot = await recall.create_bot(req.meeting_url, session["id"])
        return {"session_id": session["id"], "bot_id": bot.get("id"),
                "platform": platform, "bot": "recall"}

    # Self-hosted fallback: spawn the Playwright Meet bot as a child process
    # of THIS backend. Meet-only, and the backend must run somewhere a
    # Chromium can open (your laptop / a VM — not the 512MB Render free tier).
    if platform != "meet":
        return {"session_id": session["id"], "bot": "none",
                "error": "Self-hosted bot supports Google Meet only; "
                         "configure RECALL_API_KEY for Zoom/Teams."}
    import subprocess
    import sys
    from pathlib import Path

    # Detach the bot from THIS console: on Windows a uvicorn --reload restart
    # or Ctrl+C signals the whole process group and would take Chromium (and
    # the meeting participant) down with it. Its own console also gives the
    # user a dedicated window with the bot's logs.
    flags = 0
    if sys.platform == "win32":
        flags = (subprocess.CREATE_NEW_PROCESS_GROUP
                 | subprocess.CREATE_NEW_CONSOLE)
    argv = [sys.executable, "-m", "bot.meet_bot",
            "--meet-url", req.meeting_url,
            "--session-id", session["id"],
            "--api", "http://127.0.0.1:8000"]
    # Meet blocks anonymous guests from most calls, so reuse a signed-in
    # profile when one has been created (python -m bot.login).
    if get_settings().meet_bot_profile_dir:
        argv += ["--profile-dir", get_settings().meet_bot_profile_dir]
    proc = subprocess.Popen(
        argv,
        cwd=Path(__file__).resolve().parents[1],
        creationflags=flags,
    )
    log.info("self-hosted meet bot spawned (pid %s) in its own console — "
             "ADMIT 'TrueCandidate Observer' when it knocks; don't close the "
             "Chromium window", proc.pid)
    return {"session_id": session["id"], "bot": "self-hosted",
            "bot_pid": proc.pid, "platform": platform,
            "note": "Admit 'TrueCandidate Observer' from the Meet host controls."}


@app.get("/sessions/{session_id}/state")
async def session_state(session_id: str):
    """Debug/inspection endpoint: current scores + session ambiguity."""
    return {
        "session": await db.get_session(session_id),
        "participants": await db.get_participants(session_id),
    }
