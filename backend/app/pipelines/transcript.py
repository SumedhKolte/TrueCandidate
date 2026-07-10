"""
Transcript pipeline — orchestrates everything that happens per spoken chunk.

Per-chunk flow (all inside the < 500ms budget):
  1. Bookkeeping: speaking-ms accumulator (feeds passive filter + diarization).
  2. Greeting state machine (pure Python, ~0ms):
       interviewer says "Hi <candidate>"  -> open response window
       non-interviewer speaks in window   -> +answered_greeting
  3. ONE Groq call (analyze_chunk) returning intent score, question flag,
     and biographical claims.
  4. Signals fan out CONCURRENTLY via asyncio.gather — DB inserts and the
     Narrative Ledger contradiction check never queue behind each other.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from .. import db, groq_client, state
from ..config import get_settings
from ..models import TranscriptChunk
from ..signals import weight_of
from .events import ensure_participant, is_ui_junk
from .narrative_ledger import process_claims

log = logging.getLogger("truecandidate.transcript")

_GREETING = re.compile(
    r"\b(hi|hello|hey|welcome|good\s+(morning|afternoon|evening))[\s,!.]+([a-z]+)",
    re.IGNORECASE,
)


async def handle_chunk(chunk: TranscriptChunk) -> None:
    # A mis-targeted caption scraper can attribute speech to Meet UI chrome
    # ("Stop presenting", the clock). Never let those become participants.
    if is_ui_junk(chunk.display_name):
        log.info("dropping chunk from UI-junk name %r", chunk.display_name)
        return

    ss = state.session(chunk.session_id)
    # Register on first sighting with full interviewer/roster detection. The
    # Chrome extension sends no join events, so this is the ONLY registration
    # path in that mode.
    ps = await ensure_participant(
        ss, chunk.session_id, chunk.platform_participant_id, chunk.display_name
    )

    # 1. Speaking-time bookkeeping (async fire-and-forget patch; not score-bearing)
    ps.speaking_ms += chunk.duration_ms
    asyncio.create_task(
        db.patch_participant(ps.participant_id, total_speaking_ms=ps.speaking_ms)
    )

    tasks = []

    # 2. Greeting state machine — Target Greeting Extractor
    tasks.extend(_greeting_machine(ss, ps, chunk))

    # 3. LLM analysis — one combined call for intent + claims
    if not ps.is_interviewer and len(chunk.text.split()) >= 4:
        tasks.append(_llm_analysis(ss, ps, chunk))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _greeting_machine(ss, ps, chunk) -> list:
    """Returns awaitables to schedule; mutates the greeting window state."""
    now = time.monotonic()
    window_s = get_settings().greeting_response_window_s
    out = []

    # (a) Someone speaks while a greeting window is open. The responder both
    #     earns the signal AND gets assigned to the greeted roster candidate —
    #     in a multi-candidate room the greeting is the assignment mechanism.
    if ss.greeting and (now - ss.greeting.opened_at) <= window_s:
        if not ps.is_interviewer:
            win = ss.greeting
            ss.greeting = None  # consume the window — only the FIRST responder
            out.append(db.emit_signal(
                ss.session_id, ps.participant_id, "answered_greeting",
                weight_of("answered_greeting"),
                payload={"greeted_name": win.greeted_name,
                         "response_excerpt": chunk.text[:120]},
                source="greeting",
            ))
            if win.candidate_id and ps.matched_candidate_id is None:
                ps.matched_candidate_id = win.candidate_id
                out.append(db.patch_participant(
                    ps.participant_id, matched_candidate_id=win.candidate_id,
                ))
    elif ss.greeting:
        ss.greeting = None  # window expired

    # (b) An interviewer greets ANY roster candidate by first name.
    if ps.is_interviewer and ss.candidates:
        m = _GREETING.search(chunk.text)
        if m:
            spoken = m.group(3).lower()
            for cand in ss.candidates:
                if spoken == cand.name.split()[0].lower():
                    ss.greeting = state.GreetingWindow(
                        opened_at=now, greeted_name=spoken,
                        candidate_id=cand.candidate_id,
                    )
                    break
    return out


async def _llm_analysis(ss, ps, chunk) -> None:
    result = await groq_client.analyze_chunk(chunk.text)
    if result is None:  # timeout/failure — gracefully skip, ensemble absorbs it
        return

    emits = []
    intent = int(result.get("i", 0))
    if intent >= 70:
        # Scale the weak signal by LLM confidence: 70 -> +17, 100 -> +25.
        w = round(weight_of("intent_to_answer") * intent / 100)
        emits.append(db.emit_signal(
            ss.session_id, ps.participant_id, "intent_to_answer", w,
            payload={"intent_score": intent, "excerpt": chunk.text[:160]},
            source="intent",
        ))

    if int(result.get("q", 0)) == 1:
        emits.append(db.emit_signal(
            ss.session_id, ps.participant_id, "asked_interviewer_question",
            weight_of("asked_interviewer_question"),
            payload={"excerpt": chunk.text[:160]}, source="intent",
        ))

    claims = [c for c in result.get("c", []) if isinstance(c, str) and c.strip()]
    if claims:
        emits.append(process_claims(ss, ps, claims, chunk.text))

    if emits:
        await asyncio.gather(*emits, return_exceptions=True)
