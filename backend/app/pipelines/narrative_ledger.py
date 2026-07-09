"""
★ NOVEL HEURISTIC — The Narrative Consistency Ledger ("cross-examination memory")

The insight: an impostor can fake a name, a webcam, even a voice — but they
cannot fake a MEMORY they don't own. A real candidate telling their own story
produces biographical claims that are (a) internally consistent over 45 minutes
and (b) unique to them. TrueCandidate therefore behaves like a courtroom
cross-examiner: it remembers every atomic first-person claim each participant
makes ("worked at Stripe 3 years", "built the payments service in Go") and
scores three phenomena:

  1. CONSISTENT DEPTH (+dynamic): each new non-contradictory claim reinforces
     identity. The reward grows LOGARITHMICALLY with ledger depth
     (+3, +6, +9 … capped at +15). Why log-scaled: this models Bayesian
     evidence accumulation — early claims are weak evidence (anyone can say
     one true-sounding thing), but a deep, coherent, self-consistent narrative
     is exponentially harder to fake, so confidence should accelerate then
     saturate rather than grow linearly forever.

  2. SELF-CONTRADICTION (-30): the new claim conflicts with the same speaker's
     earlier claim (checked by a tiny Groq call against their ledger). A real
     candidate rarely contradicts their own résumé; a proxy answering
     mid-interview does. Crucially this degrades GRACEFULLY: one contradiction
     doesn't zero the score — it subtracts weak-signal weight AND raises the
     session's ambiguity (accelerating Active Probing), letting the human
     resolve it. The payload carries BOTH conflicting claims verbatim, so the
     dashboard can show the reviewer exactly what clashed.

  3. CLAIM ECHO (-25): a participant repeats a claim ANOTHER participant
     already made first-person. That is the signature of the classic proxy
     fraud pattern — a coach whispering answers, or the "real" expert taking
     over and re-telling the candidate's rehearsed story. Detected with cheap
     token-overlap (no LLM needed), so it costs ~0ms.

Why this fits TrueCandidate's philosophy: it is another WEAK signal (bounded
weights), fully explainable (every movement cites the exact claims), and its
purpose is confidence ESTIMATION under ambiguity — it turns "who is talking?"
into "whose story survives cross-examination?".
"""
from __future__ import annotations

import logging
import math
import time

from .. import db, groq_client, state
from ..signals import weight_of

log = logging.getLogger("truecandidate.ledger")

_CONSISTENT_CAP = 15   # max reward per claim once the ledger is deep
_ECHO_JACCARD = 0.6    # token-overlap threshold for cross-participant echo


def _tokens(claim: str) -> set[str]:
    return {t for t in claim.lower().split() if len(t) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


async def process_claims(
    ss: state.SessionState,
    ps: state.ParticipantState,
    claims: list[str],
    excerpt: str,
) -> None:
    for claim in claims[:3]:
        # --- 3. Claim Echo: did someone ELSE already own this story? ---------
        echo_owner = _find_echo(ss, ps, claim)
        if echo_owner is not None:
            await db.emit_signal(
                ss.session_id, ps.participant_id, "narrative_echo",
                weight_of("narrative_echo"),
                payload={"claim": claim, "first_said_by": echo_owner.display_name,
                         "excerpt": excerpt[:160]},
                source="narrative_ledger",
            )
            continue  # an echoed claim is not added to this speaker's ledger

        # --- 2. Self-contradiction check against their own ledger -------------
        if ps.claims:
            verdict = await groq_client.check_contradiction(ps.claims, claim)
            if verdict and int(verdict.get("r", 0)) == 1:
                idx = int(verdict.get("w", -1))
                prior = ps.claims[idx] if 0 <= idx < len(ps.claims) else None
                await db.emit_signal(
                    ss.session_id, ps.participant_id, "narrative_contradiction",
                    weight_of("narrative_contradiction"),
                    payload={"new_claim": claim, "contradicts": prior,
                             "excerpt": excerpt[:160]},
                    source="narrative_ledger",
                )
                # Contradictions make the ROOM more ambiguous, not just this
                # participant less likely — nudge the Active Probing clock.
                ss.ambiguous_since = ss.ambiguous_since or time.monotonic()
                continue

        # --- 1. Consistent depth: log-scaled reinforcement --------------------
        ps.claims.append(claim)
        depth = len(ps.claims)
        w = min(_CONSISTENT_CAP, round(3 * math.log2(1 + depth) + 1))
        await db.emit_signal(
            ss.session_id, ps.participant_id, "narrative_consistent", w,
            payload={"claim": claim, "ledger_depth": depth,
                     "excerpt": excerpt[:160]},
            source="narrative_ledger",
        )


def _find_echo(
    ss: state.SessionState, speaker: state.ParticipantState, claim: str
) -> state.ParticipantState | None:
    ct = _tokens(claim)
    for other in ss.participants.values():
        if other is speaker or other.is_interviewer:
            continue
        for prior in other.claims:
            if _jaccard(ct, _tokens(prior)) >= _ECHO_JACCARD:
                return other
    return None
