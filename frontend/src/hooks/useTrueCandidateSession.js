import { useEffect, useMemo, useState } from "react";
import { supabase } from "../lib/supabase";

/**
 * Live session state via Supabase Realtime.
 *
 * WHY the frontend rebuilds the timeline from the signal ledger instead of
 * asking the backend: `participant_signals` is append-only, so
 * score(t) = clamp(50 + Σ weights up to t) is reproducible ANYWHERE from the
 * raw feed. The chart is therefore a proof of the score, not a picture of it —
 * a reviewer can audit every movement. (Explainability as architecture.)
 */
export function useTrueCandidateSession(sessionId) {
  const [session, setSession] = useState(null);
  const [participants, setParticipants] = useState([]);
  const [signals, setSignals] = useState([]);
  const [candidates, setCandidates] = useState([]);

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;

    (async () => {
      const [s, p, sig, cand] = await Promise.all([
        supabase.from("interview_sessions").select("*").eq("id", sessionId).single(),
        supabase.from("live_participants").select("*").eq("session_id", sessionId),
        supabase
          .from("participant_signals")
          .select("*")
          .eq("session_id", sessionId)
          .order("created_at", { ascending: true }),
        supabase.from("session_candidates").select("*").eq("session_id", sessionId),
      ]);
      if (cancelled) return;
      setSession(s.data ?? null);
      setParticipants(p.data ?? []);
      setSignals(sig.data ?? []);
      setCandidates(cand.data ?? []);
    })();

    const channel = supabase
      .channel(`truecandidate:${sessionId}`)
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "live_participants",
          filter: `session_id=eq.${sessionId}` },
        ({ eventType, new: row }) =>
          setParticipants((prev) =>
            eventType === "INSERT"
              ? [...prev.filter((p) => p.id !== row.id), row]
              : prev.map((p) => (p.id === row.id ? row : p)),
          ),
      )
      .on(
        "postgres_changes",
        { event: "INSERT", schema: "public", table: "participant_signals",
          filter: `session_id=eq.${sessionId}` },
        ({ new: row }) => setSignals((prev) => [...prev, row]),
      )
      .on(
        "postgres_changes",
        { event: "UPDATE", schema: "public", table: "interview_sessions",
          filter: `id=eq.${sessionId}` },
        ({ new: row }) => setSession(row),
      )
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "session_candidates",
          filter: `session_id=eq.${sessionId}` },
        ({ eventType, new: row }) =>
          setCandidates((prev) =>
            eventType === "INSERT"
              ? [...prev.filter((c) => c.id !== row.id), row]
              : prev.map((c) => (c.id === row.id ? row : c)),
          ),
      )
      .subscribe();

    return () => {
      cancelled = true;
      supabase.removeChannel(channel);
    };
  }, [sessionId]);

  // Reconstruct per-participant score trajectories from the ledger.
  const timeline = useMemo(() => {
    const running = {};
    return signals.map((sig) => {
      running[sig.participant_id] = Math.max(
        0,
        Math.min(100, (running[sig.participant_id] ?? 50) + sig.weight),
      );
      return { t: new Date(sig.created_at).getTime(), ...running };
    });
  }, [signals]);

  return { session, participants, signals, timeline, candidates };
}
