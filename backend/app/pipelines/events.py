"""
Platform-event pipeline: joins, webcam toggles, screen shares, lip samples.

Cheap deterministic heuristics live here (no LLM): generic device names,
fuzzy display-name matching, screen-share credit. These fire in microseconds
and give the dashboard instant movement while the LLM signals catch up —
part of the "ensemble of weak signals" philosophy: fast/dumb + slow/smart.
"""
from __future__ import annotations

import re
import time
from difflib import SequenceMatcher

from .. import db, state
from ..signals import weight_of
from .diarization import check_lip_sample

# Device/room names that platforms assign when a human never typed a name.
_GENERIC_NAME = re.compile(
    r"^(macbook( pro| air)?|iphone|ipad|galaxy|pixel|user\d*|guest\d*|"
    r"meeting room.*|conference.*|admin|unknown|.*'s (phone|ipad|tablet))$",
    re.IGNORECASE,
)


def _name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


async def handle_event(ev) -> None:
    ss = state.session(ev.session_id)
    ps = ss.participant(ev.platform_participant_id)

    if ev.event == "participant_joined":
        await _on_join(ss, ev)
        return
    if ps is None:  # event for someone we never saw join — register them lazily
        await _on_join(ss, ev)
        ps = ss.participant(ev.platform_participant_id)

    if ev.event == "webcam_on":
        ps.webcam_on = True
        ps.webcam_off_since = None
        await db.patch_participant(ps.participant_id, webcam_on=True)
        await db.emit_signal(ev.session_id, ps.participant_id, "webcam_enabled",
                             weight_of("webcam_enabled"), source="events")

    elif ev.event == "webcam_off":
        ps.webcam_on = False
        ps.webcam_off_since = time.monotonic()
        await db.patch_participant(ps.participant_id, webcam_on=False)

    elif ev.event == "screen_share_started":
        # Interviewers share too (e.g. coding prompt) — only credit non-interviewers.
        if not ps.is_interviewer:
            await db.emit_signal(
                ev.session_id, ps.participant_id, "screen_shared",
                weight_of("screen_shared"),
                payload={"note": "initiated screen share"}, source="events",
            )

    elif ev.event == "lip_movement_sample":
        await check_lip_sample(ss, ps, float(ev.payload.get("score", 1.0)))


async def ensure_participant(
    ss: state.SessionState, session_id: str, platform_id: str, display_name: str
) -> state.ParticipantState:
    """Register a participant exactly once, with interviewer detection, generic
    name penalty, and roster matching.

    Every ingestion path funnels through here — join events (bot/Recall) AND
    the first transcript chunk from a speaker (the Chrome extension never
    sends joins). Previously only the join path did this, so in extension mode
    nobody was ever flagged as an interviewer and greetings never fired.
    """
    existing = ss.participant(platform_id)
    if existing is not None:
        return existing

    # Hydrate session metadata once (candidate roster, interviewer roster).
    if not ss.candidates:
        rows = await db.get_candidates(session_id)
        ss.candidates = [
            state.Candidate(candidate_id=c["id"], name=c["candidate_name"])
            for c in rows
        ]
        ss.interviewer_names = {
            i["display_name"] for i in await db.get_interviewers(session_id)
        }

    is_interviewer = any(
        _name_similarity(display_name, n) > 0.85 for n in ss.interviewer_names
    )
    row = await db.upsert_participant(
        session_id, platform_id, display_name, is_interviewer=is_interviewer,
    )
    ps = state.ParticipantState(
        participant_id=row["id"],
        display_name=display_name,
        is_interviewer=is_interviewer,
    )
    ss.participants[platform_id] = ps
    if is_interviewer:
        return ps

    # Instant deterministic signals on first sighting:
    if _GENERIC_NAME.match(display_name.strip()):
        await db.emit_signal(
            session_id, row["id"], "generic_device_name",
            weight_of("generic_device_name"),
            payload={"display_name": display_name}, source="events",
        )
        return ps

    # Match the display name against the WHOLE roster; best match above the
    # threshold both scores AND assigns the participant to that candidate.
    best = max(
        ss.candidates,
        key=lambda c: _name_similarity(display_name, c.name),
        default=None,
    )
    if best and _name_similarity(display_name, best.name) > 0.8:
        ps.matched_candidate_id = best.candidate_id
        await db.patch_participant(row["id"], matched_candidate_id=best.candidate_id)
        await db.emit_signal(
            session_id, row["id"], "name_matches_candidate",
            weight_of("name_matches_candidate"),
            payload={"display_name": display_name, "expected": best.name},
            source="events",
        )
    return ps


async def _on_join(ss: state.SessionState, ev) -> None:
    await ensure_participant(
        ss, ev.session_id, ev.platform_participant_id, ev.display_name
    )
