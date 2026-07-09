-- ============================================================================
-- SHERLOCK — Candidate Identification Engine
-- Migration 001: Core schema + PostgreSQL-driven scoring
--
-- WHY this design:
--   * `participant_signals` is an APPEND-ONLY ledger. We never mutate history —
--     every score change is fully auditable and replayable (explainability).
--   * Scoring is done IN the database via a trigger. The Python backend stays
--     stateless w.r.t. scores: it only *emits* signals. This means we can
--     horizontally scale N backend workers with zero coordination — Postgres
--     is the single source of truth and serializes the aggregation for us.
--   * `live_participants` is the ONLY table the frontend subscribes to for
--     scores (plus the signal feed), keeping Realtime payloads tiny.
-- ============================================================================

create extension if not exists "pgcrypto";

-- ----------------------------------------------------------------------------
-- 1. interview_sessions — one row per scheduled interview
-- ----------------------------------------------------------------------------
create table if not exists interview_sessions (
    id                uuid primary key default gen_random_uuid(),
    platform          text not null check (platform in ('zoom', 'meet', 'teams')),
    candidate_name    text not null,               -- expected candidate (from ATS)
    scheduled_at      timestamptz not null,
    status            text not null default 'pending'
                      check (status in ('pending', 'live', 'completed', 'flagged')),
    -- Session-level ambiguity state, maintained by the backend:
    --   { "entropy": 0.82, "resolution_confidence": 41,
    --     "probe_suggestion": "Ask: ...", "probe_suggested_at": "..." }
    -- Kept as jsonb (not columns) because it is advisory/derived state that
    -- evolves with the heuristics — schema churn here would be constant.
    ambiguity         jsonb not null default '{}'::jsonb,
    created_at        timestamptz not null default now()
);

-- ----------------------------------------------------------------------------
-- 2. session_interviewers — the people we KNOW are not the candidate
-- ----------------------------------------------------------------------------
create table if not exists session_interviewers (
    id            uuid primary key default gen_random_uuid(),
    session_id    uuid not null references interview_sessions(id) on delete cascade,
    display_name  text not null,
    email         text,
    unique (session_id, display_name)
);

-- ----------------------------------------------------------------------------
-- 3. live_participants — current state of everyone in the meeting room
-- ----------------------------------------------------------------------------
create table if not exists live_participants (
    id                      uuid primary key default gen_random_uuid(),
    session_id              uuid not null references interview_sessions(id) on delete cascade,
    platform_participant_id text not null,          -- ID from Zoom/Meet/Teams
    display_name            text not null,           -- may be fake ("MacBook Pro")
    is_interviewer          boolean not null default false,
    joined_at               timestamptz not null default now(),
    webcam_on               boolean not null default false,
    total_speaking_ms       integer not null default 0,
    -- Score starts at 50 = maximum uncertainty. Signals push it up or down.
    -- This is deliberate: "we don't know yet" must be visually distinct from
    -- "we are confident this is NOT the candidate" (graceful ambiguity).
    confidence_score        integer not null default 50
                            check (confidence_score between 0 and 100),
    -- Human-readable justification, rebuilt by the trigger on every signal.
    explanation             text not null default 'No signals yet — baseline uncertainty (50).',
    last_signal_at          timestamptz,
    updated_at              timestamptz not null default now(),
    unique (session_id, platform_participant_id)
);

create index if not exists idx_participants_session on live_participants (session_id);

-- ----------------------------------------------------------------------------
-- 4. participant_signals — append-only evidence ledger
-- ----------------------------------------------------------------------------
create table if not exists participant_signals (
    id              bigint generated always as identity primary key,
    session_id      uuid not null references interview_sessions(id) on delete cascade,
    participant_id  uuid not null references live_participants(id) on delete cascade,
    signal_type     text not null,     -- e.g. 'screen_shared', 'answered_greeting'
    weight          integer not null check (weight between -100 and 100),
    -- Evidence payload: transcript excerpt, Groq scores, conflicting claims…
    -- This is what makes every score movement explainable after the fact.
    payload         jsonb not null default '{}'::jsonb,
    source          text not null default 'backend',  -- pipeline that emitted it
    created_at      timestamptz not null default now()
);

create index if not exists idx_signals_participant on participant_signals (participant_id, created_at);
create index if not exists idx_signals_session on participant_signals (session_id, created_at desc);

-- Enforce append-only at the database level. Auditability is a hard invariant,
-- not a convention — even a buggy or compromised backend cannot rewrite history.
create or replace function fn_signals_immutable()
returns trigger language plpgsql as $$
begin
    raise exception 'participant_signals is append-only (attempted %)', tg_op;
end;
$$;

drop trigger if exists trg_signals_immutable on participant_signals;
create trigger trg_signals_immutable
    before update or delete on participant_signals
    for each row execute function fn_signals_immutable();

-- ----------------------------------------------------------------------------
-- 5. Scoring engine: trigger on INSERT into participant_signals
--
-- WHY a trigger instead of application code:
--   * Atomicity — the signal insert and the score update commit together.
--   * Concurrency — two pipelines emitting signals for the same participant
--     at the same instant are serialized by row-level locking on
--     live_participants; no lost updates, no distributed locks in Python.
--   * Latency — one round trip from the backend (INSERT) produces the fully
--     recalculated score AND the Realtime broadcast to the dashboard.
-- ----------------------------------------------------------------------------
create or replace function fn_recalculate_confidence()
returns trigger language plpgsql as $$
declare
    v_sum         integer;
    v_score       integer;
    v_explanation text;
begin
    -- Recompute from the full ledger (not incrementally) so the score is
    -- always a pure function of the evidence: score = clamp(50 + Σ weights).
    -- At interview scale (hundreds of signals) this SUM is microseconds and
    -- buys us total replayability.
    select coalesce(sum(weight), 0)
      into v_sum
      from participant_signals
     where participant_id = new.participant_id;

    v_score := greatest(0, least(100, 50 + v_sum));

    -- Build a compact human-readable explanation from the 3 strongest recent
    -- signals. The dashboard shows the full feed; this column is the summary.
    select string_agg(
               format('%s%s %s', case when weight >= 0 then '+' else '' end,
                      weight, replace(signal_type, '_', ' ')),
               ' · ' order by abs_weight desc)
      into v_explanation
      from (
          select signal_type, weight, abs(weight) as abs_weight
            from participant_signals
           where participant_id = new.participant_id
           order by created_at desc
           limit 12
      ) recent
     limit 3;

    update live_participants
       set confidence_score = v_score,
           explanation      = coalesce(v_explanation, 'No signals yet.'),
           last_signal_at   = new.created_at,
           updated_at       = now()
     where id = new.participant_id;

    return new;
end;
$$;

drop trigger if exists trg_recalculate_confidence on participant_signals;
create trigger trg_recalculate_confidence
    after insert on participant_signals
    for each row execute function fn_recalculate_confidence();

-- ----------------------------------------------------------------------------
-- 6. Realtime: broadcast changes to the dashboard.
--    live_participants  -> score/explanation updates (the gauge + leaderboard)
--    participant_signals -> the explanation feed / timeline events
-- ----------------------------------------------------------------------------
alter publication supabase_realtime add table live_participants;
alter publication supabase_realtime add table participant_signals;
alter publication supabase_realtime add table interview_sessions;

-- Replica identity FULL so Realtime UPDATE events carry the whole row
-- (frontend doesn't need a follow-up fetch — lower perceived latency).
alter table live_participants replica identity full;
alter table interview_sessions replica identity full;

-- ----------------------------------------------------------------------------
-- 7. Security: the dashboard uses the ANON key and must be read-only.
--    Writes only ever come from the backend's service-role key (bypasses RLS).
--    Prototype policies are open-read; production would scope reads to the
--    authenticated reviewer's org/session.
-- ----------------------------------------------------------------------------
alter table interview_sessions   enable row level security;
alter table session_interviewers enable row level security;
alter table live_participants    enable row level security;
alter table participant_signals  enable row level security;

create policy "read sessions"     on interview_sessions   for select using (true);
create policy "read interviewers" on session_interviewers for select using (true);
create policy "read participants" on live_participants    for select using (true);
create policy "read signals"      on participant_signals  for select using (true);
