"""
Signal registry — the "ensemble of weak signals".

WHY a central registry:
  * Every heuristic pipeline references weights by name, so tuning the ensemble
    is a one-file change (and can later be moved to a DB table for live tuning
    or per-customer calibration) without touching pipeline logic.
  * Weights are deliberately WEAK (|w| <= 40). No single signal can resolve a
    room on its own; confidence emerges from agreement between independent
    pipelines. That is what makes the system robust to one spoofed channel
    (fake name, camera loop, proxy speaker...).
"""
from __future__ import annotations

WEIGHTS: dict[str, int] = {
    # --- Platform events -----------------------------------------------------
    "screen_shared": 20,             # candidates share code/portfolio
    "webcam_enabled": 5,
    "generic_device_name": -15,      # "MacBook Pro", "iPhone", "Meeting Room"
    "name_matches_candidate": 15,    # fuzzy display-name match vs. ATS record

    # --- Conversation dynamics ----------------------------------------------
    "answered_greeting": 30,         # responded right after "Hi <candidate>"
    "intent_to_answer": 25,          # first-person professional storytelling
    "asked_interviewer_question": -10,  # interviewers ask; candidates answer

    # --- Passive-observer filter ----------------------------------------------
    "passive_observer": -40,         # 0 speaking + cam off for > 3 min

    # --- Diarization cross-check ----------------------------------------------
    "lip_sync_anomaly": -35,         # speaking time grows, lips don't move

    # --- Narrative Consistency Ledger (novel heuristic) -----------------------
    # narrative_consistent is DYNAMIC (grows log-scaled with ledger depth,
    # capped at +15) — see pipelines/narrative_ledger.py
    "narrative_contradiction": -30,  # contradicts their own earlier claim
    "narrative_echo": -25,           # parrots ANOTHER participant's claim
}

# Signals that must only ever fire once per participant (state transitions,
# not repeatable evidence). The emitter checks this set before inserting.
FIRE_ONCE: frozenset[str] = frozenset({
    "generic_device_name",
    "name_matches_candidate",
    "passive_observer",
})


def weight_of(signal_type: str) -> int:
    return WEIGHTS[signal_type]
