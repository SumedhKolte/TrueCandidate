"""
Async Supabase (PostgREST) layer.

WHY raw httpx instead of the supabase-py SDK:
  * One shared HTTP/2 connection pool, fully async — no sync client hiding in
    a thread pool on our 500ms budget.
  * The backend only needs 4 operations (insert signal, upsert participant,
    patch session, select). A thin layer keeps the dependency surface tiny for
    the Render deployment.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import get_settings
from .signals import FIRE_ONCE

log = logging.getLogger("truecandidate.db")

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        s = get_settings()
        _client = httpx.AsyncClient(
            base_url=f"{s.supabase_url}/rest/v1",
            headers={
                "apikey": s.supabase_service_key,
                "Authorization": f"Bearer {s.supabase_service_key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            timeout=5.0,
            http2=True,
            # Modest pool: plenty for one async worker, tiny footprint on the
            # 512MB Render free instance.
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------
async def emit_signal(
    session_id: str,
    participant_id: str,
    signal_type: str,
    weight: int,
    payload: dict[str, Any] | None = None,
    source: str = "backend",
) -> None:
    """Append one signal. The Postgres trigger does the score math + broadcast."""
    if signal_type in FIRE_ONCE and await _already_fired(participant_id, signal_type):
        return
    r = await client().post(
        "/participant_signals",
        headers={"Prefer": "return=minimal"},
        json={
            "session_id": session_id,
            "participant_id": participant_id,
            "signal_type": signal_type,
            "weight": weight,
            "payload": payload or {},
            "source": source,
        },
    )
    r.raise_for_status()
    log.info("signal %s %+d -> participant %s", signal_type, weight, participant_id)


async def _already_fired(participant_id: str, signal_type: str) -> bool:
    r = await client().get(
        "/participant_signals",
        params={
            "participant_id": f"eq.{participant_id}",
            "signal_type": f"eq.{signal_type}",
            "select": "id",
            "limit": "1",
        },
    )
    r.raise_for_status()
    return bool(r.json())


# ---------------------------------------------------------------------------
# Participants / sessions
# ---------------------------------------------------------------------------
async def upsert_participant(
    session_id: str, platform_participant_id: str, display_name: str, **fields: Any
) -> dict[str, Any]:
    r = await client().post(
        "/live_participants",
        params={"on_conflict": "session_id,platform_participant_id"},
        headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        json={
            "session_id": session_id,
            "platform_participant_id": platform_participant_id,
            "display_name": display_name,
            **fields,
        },
    )
    r.raise_for_status()
    return r.json()[0]


async def get_participants(session_id: str) -> list[dict[str, Any]]:
    r = await client().get(
        "/live_participants",
        params={"session_id": f"eq.{session_id}", "select": "*"},
    )
    r.raise_for_status()
    return r.json()


async def get_session(session_id: str) -> dict[str, Any] | None:
    r = await client().get(
        "/interview_sessions", params={"id": f"eq.{session_id}", "select": "*"}
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


async def create_session(
    platform: str, candidates: list[str], interviewers: list[str]
) -> dict[str, Any]:
    """Create a session + roster + interviewer list; returns the session row."""
    r = await client().post("/interview_sessions", json={
        "platform": platform,
        "candidate_name": candidates[0],   # legacy column; roster is canonical
        "scheduled_at": "now()",
        "status": "live",
    })
    r.raise_for_status()
    session = r.json()[0]
    for name in candidates:
        (await client().post("/session_candidates", json={
            "session_id": session["id"], "candidate_name": name,
        })).raise_for_status()
    for name in interviewers:
        (await client().post("/session_interviewers", json={
            "session_id": session["id"], "display_name": name,
        })).raise_for_status()
    return session


async def get_candidates(session_id: str) -> list[dict[str, Any]]:
    r = await client().get(
        "/session_candidates",
        params={"session_id": f"eq.{session_id}", "select": "*"},
    )
    r.raise_for_status()
    return r.json()


async def patch_candidate(candidate_id: str, **fields: Any) -> None:
    r = await client().patch(
        "/session_candidates",
        params={"id": f"eq.{candidate_id}"},
        headers={"Prefer": "return=minimal"},
        json=fields,
    )
    r.raise_for_status()


async def get_interviewers(session_id: str) -> list[dict[str, Any]]:
    r = await client().get(
        "/session_interviewers",
        params={"session_id": f"eq.{session_id}", "select": "*"},
    )
    r.raise_for_status()
    return r.json()


async def patch_session_ambiguity(session_id: str, ambiguity: dict[str, Any]) -> None:
    r = await client().patch(
        "/interview_sessions",
        params={"id": f"eq.{session_id}"},
        headers={"Prefer": "return=minimal"},
        json={"ambiguity": ambiguity},
    )
    r.raise_for_status()


async def patch_participant(participant_id: str, **fields: Any) -> None:
    r = await client().patch(
        "/live_participants",
        params={"id": f"eq.{participant_id}"},
        headers={"Prefer": "return=minimal"},
        json=fields,
    )
    r.raise_for_status()
