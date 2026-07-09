"""
End-to-end demo: seeds a MULTI-CANDIDATE session in Supabase, then replays a
scripted "crowded meeting room" against the local backend so reviewers can
watch the dashboard verify each expected candidate live.

Cast:
  Rachel Kim   — interviewer (known)
  Priya Sharma — expected candidate #1: answers her greeting, screen-shares,
                 deep consistent story                       -> VERIFIED
  Arjun Mehta  — expected candidate #2: answers his greeting, tells his own
                 distinct story                              -> VERIFIED
  MacBook Pro  — silent lurker, cam off                      -> passive observer
  Dev Patel    — the fraud: echoes Priya's claims, then his voice is heard
                 while his lips don't move                   -> flagged

Run:  python simulate.py [--api http://localhost:8000]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

CANDIDATES = ["Priya Sharma", "Arjun Mehta"]


async def seed_session() -> str:
    """Create the session + roster + known interviewer directly in Supabase."""
    async with httpx.AsyncClient(
        base_url=f"{SUPABASE_URL}/rest/v1",
        headers={"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}",
                 "Prefer": "return=representation"},
    ) as sb:
        r = await sb.post("/interview_sessions", json={
            "platform": "meet",
            "candidate_name": CANDIDATES[0],   # legacy column; roster rules now
            "scheduled_at": "2026-07-09T10:00:00Z",
            "status": "live",
        })
        r.raise_for_status()
        session_id = r.json()[0]["id"]
        for name in CANDIDATES:
            await sb.post("/session_candidates", json={
                "session_id": session_id, "candidate_name": name,
            })
        await sb.post("/session_interviewers", json={
            "session_id": session_id, "display_name": "Rachel Kim",
            "email": "rachel@acme.dev",
        })
        return session_id


async def run(api: str) -> None:
    session_id = await seed_session()
    print(f"session: {session_id}")
    ids = {name: str(uuid.uuid4())[:8] for name in
           ["rachel", "priya", "arjun", "macbook", "dev"]}

    async with httpx.AsyncClient(base_url=api, timeout=10) as c:

        async def ev(who: str, name: str, event: str, payload: dict | None = None):
            await c.post("/webhook/events", json={
                "session_id": session_id, "platform_participant_id": ids[who],
                "display_name": name, "event": event, "payload": payload or {}})

        async def say(who: str, name: str, text: str, dur_ms: int = 6000):
            await c.post("/webhook/transcript", json={
                "session_id": session_id, "platform_participant_id": ids[who],
                "display_name": name, "text": text,
                "started_at_ms": 0, "duration_ms": dur_ms})
            print(f"  {name}: {text[:70]}")

        # --- everyone joins ---------------------------------------------------
        await ev("rachel", "Rachel Kim", "participant_joined")
        await ev("priya", "Priya Sharma", "participant_joined")   # name match #1
        await ev("arjun", "Arjun M", "participant_joined")        # partial name
        await ev("macbook", "MacBook Pro", "participant_joined")  # -15 instantly
        await ev("dev", "Dev Patel", "participant_joined")
        await ev("priya", "Priya Sharma", "webcam_on")
        await ev("arjun", "Arjun M", "webcam_on")
        await ev("dev", "Dev Patel", "webcam_on")
        await asyncio.sleep(2)

        # --- greeting handshake #1: Priya --------------------------------------
        await say("rachel", "Rachel Kim",
                  "Hi Priya, thanks for joining! How are you today?")
        await asyncio.sleep(1.5)
        await say("priya", "Priya Sharma",
                  "Hi Rachel! Doing great, thank you — excited for this.")
        await asyncio.sleep(2)

        # --- Priya builds a deep, consistent narrative ------------------------
        await say("rachel", "Rachel Kim",
                  "Priya, tell me about your most recent role.")
        await asyncio.sleep(1)
        await say("priya", "Priya Sharma",
                  "In my last role at Streamline I spent three years on the "
                  "payments team, and I built our reconciliation service in Go.")
        await asyncio.sleep(2)
        await say("priya", "Priya Sharma",
                  "I designed the retry pipeline myself — when I migrated us "
                  "off cron to a queue, failure rates dropped forty percent.")
        await asyncio.sleep(2)
        await ev("priya", "Priya Sharma", "screen_share_started")   # +20
        await say("priya", "Priya Sharma",
                  "Let me share my screen — this is the architecture I drew "
                  "for that reconciliation flow.")
        await asyncio.sleep(2)

        # --- greeting handshake #2: Arjun --------------------------------------
        await say("rachel", "Rachel Kim",
                  "Thanks Priya. Hello Arjun, welcome — can you hear us okay?")
        await asyncio.sleep(1.5)
        await say("arjun", "Arjun M",
                  "Hi Rachel, yes loud and clear. Happy to be here.")
        await asyncio.sleep(2)
        await say("rachel", "Rachel Kim",
                  "Arjun, walk me through something you shipped recently.")
        await asyncio.sleep(1)
        await say("arjun", "Arjun M",
                  "Sure — at Nimbus I led the mobile checkout rewrite in "
                  "React Native, and I cut our crash rate by half last year.")
        await asyncio.sleep(2)
        await say("arjun", "Arjun M",
                  "I also built the offline cart sync myself, that was the "
                  "hardest part — conflict resolution across devices.")
        await asyncio.sleep(2)

        # --- Dev the proxy: echoes Priya's story, lips don't move -------------
        await say("dev", "Dev Patel",
                  "Yeah, I built the reconciliation service in Go on the "
                  "payments team for three years.", dur_ms=7000)   # narrative_echo
        await ev("dev", "Dev Patel", "lip_movement_sample", {"score": 0.05})
        await asyncio.sleep(2)

        # --- lip samples: real candidates are normal ---------------------------
        await ev("priya", "Priya Sharma", "lip_movement_sample", {"score": 0.9})
        await ev("arjun", "Arjun M", "lip_movement_sample", {"score": 0.85})

        print("\nScripted room replayed. Watch the Candidate Roster strip:")
        print("Priya and Arjun should climb toward IDENTIFIED / VERIFIED,")
        print("'MacBook Pro' takes the passive-observer penalty after ~3 min,")
        print("and Dev Patel gets flagged for echo + lip-sync anomaly.")
        print(f"\nDashboard session id: {session_id}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://localhost:8000")
    asyncio.run(run(p.parse_args().api))
