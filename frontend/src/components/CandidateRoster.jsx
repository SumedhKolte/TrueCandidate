import { motion } from "framer-motion";
import { colorFor } from "../lib/supabase";
import AnimatedNumber from "./AnimatedNumber";

/**
 * The Candidate Roster — one card per EXPECTED candidate, answering the
 * interviewer's actual question: "is each of my candidates verified yet?"
 *
 * Status comes from the backend sweep (best-matched participant's score):
 *   VERIFIED >= 85 · IDENTIFIED >= 65 · FLAGGED < 35 · PENDING otherwise.
 * Statuses always ship icon + label, never color alone.
 */
const STATUS = {
  verified:   { label: "VERIFIED",   icon: "✓", cls: "border-good/50 bg-good/10 text-good" },
  identified: { label: "IDENTIFIED", icon: "◐", cls: "border-accent/50 bg-accent/10 text-accent" },
  pending:    { label: "PENDING",    icon: "…", cls: "border-edge bg-surface text-muted" },
  flagged:    { label: "FLAGGED",    icon: "⚠", cls: "border-bad/50 bg-bad/10 text-bad" },
};

export default function CandidateRoster({ candidates, participants }) {
  if (!candidates.length) return null;

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {candidates.map((cand, i) => {
        const matched = participants
          .filter((p) => p.matched_candidate_id === cand.id)
          .sort((a, b) => b.confidence_score - a.confidence_score)[0];
        const st = STATUS[cand.status] ?? STATUS.pending;

        return (
          <motion.div
            key={cand.id}
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ type: "spring", stiffness: 140, damping: 20, delay: i * 0.06 }}
            className={`panel relative overflow-hidden p-4 ${
              cand.status === "verified" ? "leader-glow" : ""
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-sm font-medium">{cand.candidate_name}</span>
              <motion.span
                key={cand.status}
                initial={{ scale: 0.7, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={{ type: "spring", stiffness: 400, damping: 18 }}
                className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold tracking-widest ${st.cls}`}
              >
                {st.icon} {st.label}
              </motion.span>
            </div>

            {matched ? (
              <div className="mt-3 flex items-center gap-2">
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ background: colorFor(participants, matched.id) }}
                  aria-hidden
                />
                <span className="truncate text-xs text-ink-2">
                  {matched.display_name}
                </span>
                <AnimatedNumber
                  value={matched.confidence_score}
                  className="ml-auto text-xl font-semibold tabular-nums"
                />
              </div>
            ) : (
              <p className="mt-3 text-xs text-muted">No participant matched yet</p>
            )}

            {/* progress toward the verification threshold (85) */}
            <div className="mt-2 h-1 overflow-hidden rounded bg-grid">
              <motion.div
                className={`h-full rounded ${
                  cand.status === "flagged" ? "bg-bad" : "bg-good"
                }`}
                animate={{
                  width: `${Math.min(100, ((matched?.confidence_score ?? 0) / 85) * 100)}%`,
                }}
                transition={{ type: "spring", stiffness: 70, damping: 18 }}
              />
            </div>
          </motion.div>
        );
      })}
    </div>
  );
}
