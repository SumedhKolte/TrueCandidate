-- ============================================================================
-- Migration 002: Multi-candidate sessions
--
-- One meeting can now contain SEVERAL expected candidates (panel/group
-- interviews, back-to-back slots in one room). The question changes from
-- "who is the candidate?" to "for EACH expected candidate, which participant
-- is them — and are we confident enough to call them VERIFIED?"
--
-- Design: the signal ledger and scoring trigger are untouched. A participant's
-- confidence score still means "how strongly the evidence says this person is
-- a real, present candidate". What's new is the ASSIGNMENT layer: each
-- participant can be matched to one roster entry (by name match or greeting
-- response), and each roster entry carries a verification status derived from
-- its best-matched participant's score.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. The candidate roster for a session
-- ----------------------------------------------------------------------------
create table if not exists session_candidates (
    id              uuid primary key default gen_random_uuid(),
    session_id      uuid not null references interview_sessions(id) on delete cascade,
    candidate_name  text not null,          -- from the ATS
    email           text,
    -- pending    -> no participant confidently matched yet
    -- identified -> best match >= 65 (working hypothesis)
    -- verified   -> best match >= 85 (safe to proceed)
    -- flagged    -> best match's ledger contains fraud-class signals
    status          text not null default 'pending'
                    check (status in ('pending', 'identified', 'verified', 'flagged')),
    updated_at      timestamptz not null default now(),
    unique (session_id, candidate_name)
);

create index if not exists idx_candidates_session on session_candidates (session_id);

-- ----------------------------------------------------------------------------
-- 2. Participant -> candidate assignment
-- ----------------------------------------------------------------------------
alter table live_participants
    add column if not exists matched_candidate_id uuid
        references session_candidates(id) on delete set null;

-- ----------------------------------------------------------------------------
-- 3. Realtime + read-only access for the dashboard
-- ----------------------------------------------------------------------------
alter publication supabase_realtime add table session_candidates;
alter table session_candidates replica identity full;

alter table session_candidates enable row level security;
create policy "read candidates" on session_candidates for select using (true);

-- ----------------------------------------------------------------------------
-- 4. Back-compat: keep interview_sessions.candidate_name working by copying
--    it into the roster for existing sessions that have no roster rows yet.
-- ----------------------------------------------------------------------------
insert into session_candidates (session_id, candidate_name)
select s.id, s.candidate_name
  from interview_sessions s
 where not exists (select 1 from session_candidates c where c.session_id = s.id)
on conflict do nothing;
