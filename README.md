# TrueCandidate — Real-Time Candidate Identification Engine

Given a crowded Zoom/Meet/Teams interview room, TrueCandidate identifies
**who is actually the candidate** — in real time, with a continuously
updating confidence score per person and a plain-language explanation for
every point that score moves.

It answers three questions live, throughout the call:

- *Which participant is actually the candidate, not an interviewer, a silent
  observer, or someone joining under a fake name like "MacBook Pro"?*
- *In a panel interview with several expected candidates, which ones are
  verified and which are still unresolved?*
- *If the evidence is genuinely ambiguous, what one question would resolve it —
  and who should the interviewer ask?*

## How it works

Nothing in this system is one big "AI, tell me who the candidate is" prompt.
Instead, independent pipelines each watch the meeting for one narrow kind of
evidence and emit small, bounded "signals" — a screen share is worth +20, a
generic device name is worth −15, going silent with the camera off for three
minutes is worth −40. No single signal can ever decide the outcome; a
person's score is just the sum of everything observed about them, clamped to
0–100. That's deliberate: it's what makes the system hard to fool by spoofing
one channel (a fake name, a looped camera, a proxy voice) — the other
signals still see through it.

Those signals are captured from a live meeting (by a Chrome extension or a
bot — see below), scored by a database trigger the instant they arrive, and
streamed to a dashboard that shows exactly which signal caused every change
in the score. The whole system is built around three ideas:

1. **Weak signals, strong ensemble.** Every individual signal is weak by
   design (bounded weight, easy to explain, cheap to compute). Confidence
   only builds when several independent signals agree, so the system
   degrades gracefully instead of confidently guessing.
2. **The database owns the score.** The backend never computes a score
   itself — it only appends evidence. A Postgres trigger recalculates
   `score = clamp(50 + sum of that person's signal weights, 0, 100)` the
   instant a new signal lands, in the same transaction that broadcasts the
   update. This means any number of backend instances can emit signals
   concurrently with zero coordination, and the full history of *why* a
   score is what it is lives permanently in an append-only ledger table.
3. **Explainability is structural, not cosmetic.** The dashboard doesn't
   just show a number — it rebuilds the entire score history for every
   participant from that same ledger, client-side. The chart is a replay of
   the evidence, not a chart library's guess at smoothing a value.

## Architecture

```
 ┌─────────────────────────── CAPTURE LAYER (pick one) ───────────────────────────┐
 │                                                                                  │
 │  Chrome extension          Self-hosted bot            Recall.ai bot            │
 │  (reads captions in        (Playwright + Chromium      (hosted meeting-bot     │
 │   your own Meet tab,        joins the call as a         API; covers Zoom      │
 │   no bot, no account)       guest, scrapes captions)     and Teams too)        │
 │                                                                                  │
 └──────────────────────────────────┬───────────────────────────────────────────┘
                                     │  HTTPS webhooks
                                     │  POST /webhook/transcript   (who said what)
                                     │  POST /webhook/events       (join/webcam/screen-share)
                                     ▼
                    ┌────────────────────────────────────┐
                    │      FastAPI backend (Render)        │
                    │                                      │
                    │  Signal pipelines (independent):      │
                    │   • greeting response matcher         │
                    │   • Groq: intent-to-answer + claims    │──→ Groq (Llama 3.3 70B)
                    │   • Narrative Consistency Ledger       │    one combined call/chunk,
                    │   • passive-observer sweep             │    <450ms hard deadline
                    │   • lip-sync / diarization cross-check │
                    │   • active-probing + ambiguity meter    │
                    │                                      │
                    │  Each pipeline only APPENDS signals — │
                    │  it never computes a score itself.    │
                    └───────────────────┬────────────────────┘
                                         │  INSERT INTO participant_signals
                                         ▼
                    ┌────────────────────────────────────┐
                    │      Supabase Postgres               │
                    │                                      │
                    │  Trigger, fired on every signal:      │
                    │    score = clamp(50 + Σ weights)     │
                    │    → updates live_participants        │
                    │    → updates session_candidates status│
                    │                                      │
                    │  Realtime broadcasts the change        │
                    └───────────────────┬────────────────────┘
                                         │  Postgres Realtime (WebSocket)
                                         ▼
                    ┌────────────────────────────────────┐
                    │   React dashboard (Vercel)           │
                    │                                      │
                    │   confidence gauge · candidate roster │
                    │   score timeline (rebuilt from the    │
                    │   raw signal ledger) · explanation     │
                    │   feed · active-probe suggestions      │
                    └────────────────────────────────────┘
```

Everything downstream of "a signal was appended" is identical no matter which
capture layer produced it — the extension, the self-hosted bot, and Recall
all post to the exact same two webhooks.

## Signal ensemble

| Signal | Weight | Pipeline |
|---|---|---|
| `answered_greeting` | +30 | greeting state machine (regex, 0ms) |
| `intent_to_answer` | up to +25 | Groq: first-person professional storytelling |
| `screen_shared` | +20 | platform events |
| `name_matches_candidate` | +15 | fuzzy match vs expected candidate name |
| `narrative_consistent` | +3…+15 (log-scaled) | Narrative Consistency Ledger |
| `webcam_enabled` | +5 | platform events |
| `asked_interviewer_question` | −10 | Groq |
| `generic_device_name` | −15 | regex ("MacBook Pro", "Meeting Room…") |
| `narrative_echo` | −25 | ledger, token-overlap |
| `narrative_contradiction` | −30 | ledger, Groq cross-exam |
| `lip_sync_anomaly` | −35 | diarization × lip-movement cross-check |
| `passive_observer` | −40 | background sweep (silent + cam off > 3 min) |

Session-level, normalized Shannon entropy over everyone's scores is published
as `interview_sessions.ambiguity` ("how unresolved is this room?"), and
**active probing** drafts a chat question for the interviewer when the room
stays ambiguous for more than 5 minutes.

## Multi-candidate sessions (panel / group interviews)

A session carries a roster of expected candidates (`session_candidates`).
Participants are assigned to roster entries by display-name matching and by
answering their own greeting — "Hi Arjun…" maps whoever responds to Arjun —
and a background sweep derives a verification status per candidate from
their best-matched participant's score:

| Status | Meaning |
|---|---|
| `verified` | best match ≥ 85 — safe to proceed |
| `identified` | best match ≥ 65 — working hypothesis |
| `pending` | nobody confidently matched yet |
| `flagged` | best match < 35 — evidence *against* the match |

The dashboard's candidate roster strip shows one card per expected candidate
flipping PENDING → IDENTIFIED → VERIFIED live.

## The Narrative Consistency Ledger

> An impostor can fake a name, a webcam, even a voice — but they cannot fake
> a memory they don't own.

This is the system's most distinctive heuristic. TrueCandidate behaves like a
courtroom cross-examiner: every atomic first-person biographical claim a
participant makes ("worked at Streamline 3 years", "built the reconciliation
service in Go") is extracted by the same Groq call that scores intent — zero
extra latency — and appended to that participant's claim ledger. Three
phenomena get scored from it:

1. **Consistent depth (+3…+15, log-scaled).** Each new non-contradictory
   claim reinforces identity, with the reward growing logarithmically in
   ledger depth. One plausible claim is weak evidence; a self-consistent
   narrative built over 45 minutes is exponentially hard to fake.
2. **Self-contradiction (−30).** A tiny Groq call cross-examines each new
   claim against the speaker's own ledger. Contradictions degrade
   *gracefully* — the score dips, both clashing claims are quoted verbatim in
   the dashboard feed, and the session's ambiguity clock accelerates so
   active probing fires sooner. The human resolves it, not the machine.
3. **Claim echo (−25).** If participant B repeats a claim participant A
   already made first-person (cheap token-overlap, no LLM needed), that's the
   signature of proxy fraud — a hidden expert re-telling the candidate's
   rehearsed story.

Code: [backend/app/pipelines/narrative_ledger.py](backend/app/pipelines/narrative_ledger.py)

## Running it locally

### 1. Database

Apply both migrations in the Supabase SQL editor (or `supabase db push`), in order:

```
supabase/migrations/001_sherlock_schema.sql
supabase/migrations/002_multi_candidate.sql
```

### 2. Backend

```bash
cd backend
cp .env.example .env          # fill in Supabase + Groq keys
python -m venv venv && venv\Scripts\activate   # Windows; use source venv/bin/activate elsewhere
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### 3. Frontend

```bash
cd frontend
cp .env.example .env          # Supabase URL + anon key
npm install && npm run dev
```

### 4. Scripted demo (no real meeting needed)

```bash
cd backend
python simulate.py            # replays a scripted crowded room
```

Open the dashboard, paste the printed session id (or use `?session=<id>` in
the URL), and watch two candidates get verified while a silent lurker and a
proxy speaker get penalized and flagged.

## Loading the Chrome extension

This is the recommended way to observe a **real** meeting: it runs inside the
interviewer's own Meet tab and reads the captions already on screen. No bot
joins the call, nobody has to admit anything, and no Google account is
needed anywhere.

1. Open `chrome://extensions` in Chrome.
2. Turn on **Developer mode** (top-right toggle).
3. Click **Load unpacked** and select the `extension/` folder from this repo.
4. The TrueCandidate icon appears in your toolbar.

**Using it in a meeting:**

1. Start the backend (`uvicorn app.main:app`) and make sure `frontend` env
   vars point at it (or use the deployed Render URL).
2. Join the Google Meet as normal, and turn on **live captions** (the CC
   button) — the extension reads those captions, so they must be on.
3. Click the TrueCandidate icon. Fill in:
   - **Backend URL** — `http://localhost:8000` for local dev, or your Render URL.
   - **Your name as it appears in Meet** — required. Meet labels your own
     captions "You"; this tells TrueCandidate who that actually is.
   - **Expected candidates** — comma-separated names.
   - **Interviewers** — comma-separated; these are excluded from scoring.
4. Click **Start observing**. A small badge appears in the corner of the
   Meet: green with a live chunk counter once captions are flowing, amber if
   it can't find them yet.
5. Copy the session id from the one-click **Copy** button in the popup and
   paste it into the dashboard (or open `?session=<id>` directly).

**If the badge stays at 0 chunks**, open DevTools in the Meet tab and run
`window.__trueCandidateDebug()` in the console. It reports which caption
discovery strategy matched (if any), what each strategy currently sees, and
every `aria-live` region on the page — Google reshuffles Meet's DOM
periodically, and the fix is always in `findCaptionRoot()` inside
[extension/content.js](extension/content.js).

### Other ways to capture a meeting

Two more capture layers exist, both posting to the same two webhooks:

- **Self-hosted Playwright bot** ([backend/bot/meet_bot.py](backend/bot/meet_bot.py)) —
  a real Chromium browser joins the call as a guest named "TrueCandidate
  Observer" and scrapes captions the same way the extension does. Useful
  when you can't install a browser extension. Google blocks anonymous guests
  from most meetings, so run `python -m bot.login` once to create a
  signed-in Chrome profile, then set `MEET_BOT_PROFILE_DIR` in
  `backend/.env`. Needs a machine that can open a browser window — not the
  free Render tier.
- **Recall.ai** — a hosted meeting-bot API that also covers Zoom and Teams.
  Set `RECALL_API_KEY`, `RECALL_API_BASE`, and `PUBLIC_BASE_URL` in
  `backend/.env`; `POST /sessions/launch` will dispatch a Recall bot instead
  of the local one whenever a key is configured.

Speaker identity never depends on voice recognition in any mode — whichever
capture layer is in the call already knows which participant tile produced
each line of text, so "who said this" comes for free from the platform
itself.

> Joining and analyzing a call requires the consent of everyone in it —
> announce the extension or bot before you start observing.

## Deployment (Render free tier + Vercel)

**Backend → Render** — [render.yaml](render.yaml) is a ready Blueprint:

1. Push this repo to GitHub, then Render → New → Blueprint → pick the repo.
2. Fill in `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `GROQ_API_KEY` when prompted.
3. Done — health check on `/health`, one uvicorn worker.

The whole backend is async and does no ML inference locally (Groq does that,
Postgres does the scoring), which is what makes it fit the free 512MB
instance. The free tier sleeps after ~15 minutes idle; the first webhook
after a nap takes ~30s to wake it.

**Frontend → Vercel**:

1. Vercel → New Project → import the repo → set **Root Directory** to `frontend`.
2. Add env vars `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, and
   `VITE_API_URL` (your Render backend URL).
3. Deploy — [vercel.json](frontend/vercel.json) handles SPA routing.

After both are live, lock `allow_origins` in
[backend/app/main.py](backend/app/main.py) down to your Vercel domain, and
point the extension's **Backend URL** field at the Render URL instead of
localhost.

## Repo map

```
supabase/migrations/                          schema, scoring trigger, multi-candidate roster, RLS
backend/app/main.py                           FastAPI entry point, webhooks, background sweeps
backend/app/groq_client.py                    latency-budgeted LLM calls
backend/app/signals.py                        the weak-signal weight registry
backend/app/pipelines/                        one file per heuristic
backend/bot/meet_bot.py                       self-hosted Playwright Meet bot
backend/bot/login.py                          one-time Google sign-in for the bot
backend/simulate.py                           scripted demo room
extension/                                    Chrome extension capture layer
frontend/src/hooks/useTrueCandidateSession.js Realtime subscription + ledger replay
frontend/src/components/                      gauge · roster · timeline · explanation feed
```
#   T r u e C a n d i d a t e  
 