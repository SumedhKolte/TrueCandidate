"""
In-memory per-session working state.

WHY in-memory and not in Postgres:
  * This is HOT, high-churn, low-value-if-lost state (greeting windows,
    speaking-ms accumulators, claim ledgers). Persisting every tick would
    hammer the DB for data nobody audits. Everything that MATTERS — signals
    and scores — is durably in Postgres; this cache can be rebuilt from a
    restart with at most a few seconds of degraded heuristics.
  * For multi-worker scale-out, swap this module for Redis with the same
    interface — the pipelines only talk to SessionState, never to the dict.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ParticipantState:
    participant_id: str                     # live_participants.id (uuid)
    display_name: str
    is_interviewer: bool = False
    joined_at: float = field(default_factory=time.monotonic)
    speaking_ms: int = 0
    webcam_on: bool = False
    webcam_off_since: float | None = field(default_factory=time.monotonic)
    last_lip_score: float = 1.0             # 1.0 = normal lip movement
    speaking_ms_at_last_lip_check: int = 0
    claims: list[str] = field(default_factory=list)   # Narrative Ledger
    matched_candidate_id: str | None = None           # roster assignment


@dataclass
class GreetingWindow:
    """Open after an interviewer says 'Hi <candidate>' — first non-interviewer
    to speak inside the window earns `answered_greeting` and is mapped to the
    greeted roster candidate."""
    opened_at: float
    greeted_name: str
    candidate_id: str | None = None   # session_candidates.id being greeted


@dataclass
class Candidate:
    candidate_id: str                 # session_candidates.id
    name: str


@dataclass
class SessionState:
    session_id: str
    candidates: list[Candidate] = field(default_factory=list)  # expected roster
    interviewer_names: set[str] = field(default_factory=set)
    participants: dict[str, ParticipantState] = field(default_factory=dict)  # by platform id
    greeting: GreetingWindow | None = None
    ambiguous_since: float | None = None     # for Active Probing
    last_probe_at: float | None = None
    last_activity: float = field(default_factory=time.monotonic)

    def participant(self, platform_id: str) -> ParticipantState | None:
        return self.participants.get(platform_id)

    def touch(self) -> None:
        self.last_activity = time.monotonic()


_sessions: dict[str, SessionState] = {}

# Every meeting ever observed by this process otherwise lives in _sessions
# forever: nothing ever removes a finished session, so the background sweep
# (passive.sweep / probing.sweep, every ~20s) keeps hitting Supabase and Groq
# for sessions nobody is watching anymore. Prune anything idle this long.
STALE_AFTER_S = 30 * 60


def session(session_id: str) -> SessionState:
    if session_id not in _sessions:
        _sessions[session_id] = SessionState(session_id=session_id)
    ss = _sessions[session_id]
    ss.touch()
    return ss


def all_sessions() -> list[SessionState]:
    return list(_sessions.values())


def prune_stale() -> None:
    now = time.monotonic()
    stale = [sid for sid, ss in _sessions.items()
             if now - ss.last_activity > STALE_AFTER_S]
    for sid in stale:
        del _sessions[sid]
