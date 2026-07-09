"""
Active Probing + ambiguity estimation + candidate verification — background sweep.

Three jobs per session:
  1. CANDIDATE VERIFICATION: for every roster candidate, find their
     best-matched participant and derive a status —
       verified   score >= 85   ("safe to proceed")
       identified score >= 65   (working hypothesis)
       flagged    score <  35   (evidence AGAINST the match: fraud-class hits)
       pending    otherwise / nobody matched yet
     This is the answer to "is each of my candidates at high confidence?" —
     the dashboard shows one card per roster candidate flipping to VERIFIED.
  2. AMBIGUITY METER: normalized Shannon entropy over softmaxed participant
     scores, published to interview_sessions.ambiguity. We never pretend to
     know; we quantify how much we don't.
  3. ACTIVE PROBING: if any roster candidate stays unresolved beyond the
     window, ask Groq to draft ONE chat question targeting the unresolved
     candidates by name. TrueCandidate suggests; the interviewer decides.
"""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone

from .. import db, groq_client, state
from ..config import get_settings

log = logging.getLogger("truecandidate.probing")

VERIFIED_AT = 85
IDENTIFIED_AT = 65
FLAGGED_BELOW = 35


def _entropy(scores: list[int]) -> float:
    """Normalized Shannon entropy of softmax(scores/10). 1.0 = coin flip."""
    if len(scores) < 2:
        return 0.0
    exps = [math.exp(s / 10) for s in scores]
    total = sum(exps)
    probs = [e / total for e in exps]
    h = -sum(p * math.log(p) for p in probs if p > 0)
    return h / math.log(len(scores))


def _status_for(best_score: int | None) -> str:
    if best_score is None:
        return "pending"
    if best_score >= VERIFIED_AT:
        return "verified"
    if best_score >= IDENTIFIED_AT:
        return "identified"
    if best_score < FLAGGED_BELOW:
        return "flagged"
    return "pending"


async def sweep() -> None:
    cfg = get_settings()
    now = time.monotonic()

    for ss in state.all_sessions():
        rows = [p for p in await db.get_participants(ss.session_id)
                if not p["is_interviewer"]]
        if not rows:
            continue
        rows.sort(key=lambda r: r["confidence_score"], reverse=True)
        scores = [r["confidence_score"] for r in rows]

        # --- 1. Per-candidate verification --------------------------------
        unresolved: list[str] = []
        for cand in ss.candidates:
            matched = [r for r in rows
                       if r.get("matched_candidate_id") == cand.candidate_id]
            best = max((r["confidence_score"] for r in matched), default=None)
            status = _status_for(best)
            if status != "verified":
                unresolved.append(cand.name)
            await db.patch_candidate(cand.candidate_id, status=status)

        # --- 2. Session ambiguity ------------------------------------------
        ambiguous = bool(unresolved) and len(rows) >= 2
        ambiguity: dict = {
            "entropy": round(_entropy(scores), 3),
            "unresolved_candidates": unresolved,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }

        # --- 3. Active probing ---------------------------------------------
        if ambiguous:
            ss.ambiguous_since = ss.ambiguous_since or now
            stuck_for = now - ss.ambiguous_since
            probe_cooldown_ok = (
                ss.last_probe_at is None
                or now - ss.last_probe_at > cfg.probe_after_ambiguous_s
            )
            if stuck_for >= cfg.probe_after_ambiguous_s and probe_cooldown_ok:
                contenders = [
                    {"name": r["display_name"], "score": r["confidence_score"],
                     "evidence": r["explanation"]}
                    for r in rows[:4]
                ]
                probe = await groq_client.suggest_probe(
                    ", ".join(unresolved), contenders
                )
                if probe:
                    ss.last_probe_at = now
                    ambiguity["probe_suggestion"] = probe
                    ambiguity["probe_suggested_at"] = ambiguity["computed_at"]
                    log.info("probe suggested for %s: %s", ss.session_id, probe)
        else:
            ss.ambiguous_since = None

        await db.patch_session_ambiguity(ss.session_id, ambiguity)
