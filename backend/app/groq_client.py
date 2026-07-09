"""
Groq inference layer — tuned for the < 500ms pipeline budget.

Latency tactics (the "Why"):
  1. ONE combined call per transcript chunk. Intent scoring and biographical
     claim extraction share a single prompt instead of two round trips.
  2. Responses are forced into a MINIMAL JSON shape with single-letter keys —
     output tokens dominate LLM latency, so we pay for ~15 tokens, not 200.
  3. `asyncio.wait_for` enforces a hard deadline. On timeout we DROP the LLM
     signal for that chunk and keep going — a missing weak signal is fine
     (the ensemble absorbs it); a stalled pipeline is not.
  4. temperature=0 + max_tokens cap: deterministic, bounded, cheap.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from groq import AsyncGroq

from .config import get_settings

log = logging.getLogger("truecandidate.groq")

_groq: AsyncGroq | None = None


def _client() -> AsyncGroq:
    global _groq
    if _groq is None:
        _groq = AsyncGroq(api_key=get_settings().groq_api_key)
    return _groq


async def _ask(
    system: str, user: str, max_tokens: int, timeout_s: float | None = None
) -> dict[str, Any] | None:
    """Fire one JSON-mode completion under the hard latency budget."""
    s = get_settings()
    budget = timeout_s if timeout_s is not None else s.groq_timeout_s
    try:
        resp = await asyncio.wait_for(
            _client().chat.completions.create(
                model=s.groq_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            ),
            timeout=budget,
        )
        return json.loads(resp.choices[0].message.content)
    except asyncio.TimeoutError:
        log.warning("groq call exceeded %.0fms budget — signal skipped",
                    budget * 1000)
        return None
    except Exception:  # malformed JSON, rate limit, network — never crash a webhook
        log.exception("groq call failed — signal skipped")
        return None


# ---------------------------------------------------------------------------
# Combined chunk analysis: intent-to-answer + claim extraction in ONE call.
#
# Output contract (single-letter keys keep completions ~10-20 tokens):
#   i: 0-100  intent-to-answer score (first-person professional storytelling)
#   q: 0/1    speaker is asking an interviewer-style question
#   c: []     atomic first-person biographical claims, normalized
# ---------------------------------------------------------------------------
_CHUNK_SYSTEM = (
    "You classify one utterance from a job interview. Reply ONLY minified JSON: "
    '{"i":<0-100 int, how strongly the speaker is ANSWERING as the candidate — '
    "first-person professional storytelling like 'I built', 'in my last role'>,"
    '"q":<1 if the speaker is asking an interview question of someone else, else 0>,'
    '"c":[<up to 3 atomic first-person biographical claims as short normalized '
    'strings, e.g. "worked at Stripe 3 years", "built payments service in Go". '
    "Empty list if none>]}"
)


async def analyze_chunk(text: str) -> dict[str, Any] | None:
    return await _ask(_CHUNK_SYSTEM, text[:600], max_tokens=90)


# ---------------------------------------------------------------------------
# Claim comparison for the Narrative Consistency Ledger.
#   r: 1 = new claim CONTRADICTS a prior claim, 0 = consistent/unrelated
#   w: index of the contradicted prior claim (or -1)
# ---------------------------------------------------------------------------
_CLAIM_SYSTEM = (
    "Given prior claims (JSON list) and one new claim from the same speaker in "
    "an interview, decide if the new claim factually CONTRADICTS any prior one "
    '(different employer/duration/tech for the same story). Reply ONLY '
    '{"r":<0|1>,"w":<index of contradicted claim or -1>}'
)


async def check_contradiction(prior: list[str], new_claim: str) -> dict[str, Any] | None:
    payload = json.dumps({"prior": prior[-12:], "new": new_claim})
    return await _ask(_CLAIM_SYSTEM, payload, max_tokens=16)


# ---------------------------------------------------------------------------
# Active Probing: craft one disambiguating question for the interviewer.
# This path is NOT latency-critical (fires at most every 5 min), but we still
# cap tokens — the suggestion must fit in a chat message.
# ---------------------------------------------------------------------------
_PROBE_SYSTEM = (
    "You help an interviewer verify which meeting participants are the real "
    "candidates. Given the unresolved candidate name(s) and the participants "
    "with their observed evidence, reply ONLY "
    '{"p":"<one short, natural question the interviewer should ask, addressed '
    'to an unresolved candidate by name, designed so only the real candidate '
    "answers fluently>\"}"
)


async def suggest_probe(unresolved: str, contenders: list[dict[str, Any]]) -> str | None:
    payload = json.dumps({"unresolved_candidates": unresolved,
                          "contenders": contenders})
    # Relaxed 3s budget: probing is a background task, not in the webhook path.
    out = await _ask(_PROBE_SYSTEM, payload, max_tokens=60, timeout_s=3.0)
    return out.get("p") if out else None
