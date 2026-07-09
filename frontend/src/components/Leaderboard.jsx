import { AnimatePresence, motion } from "framer-motion";
import { colorFor } from "../lib/supabase";
import AnimatedNumber from "./AnimatedNumber";

/**
 * Participant leaderboard. `layout` on each row makes rank changes a smooth
 * spring re-sort — you SEE the moment someone overtakes the leader. The color
 * chip is the participant's fixed series hue (same as their timeline line).
 */
export default function Leaderboard({ participants, candidates = [] }) {
  const ranked = [...participants]
    .filter((p) => !p.is_interviewer)
    .sort((a, b) => b.confidence_score - a.confidence_score);
  const interviewers = participants.filter((p) => p.is_interviewer);
  const candidateName = (id) =>
    candidates.find((c) => c.id === id)?.candidate_name;

  return (
    <div className="space-y-2">
      <AnimatePresence>
        {ranked.map((p, i) => (
          <motion.div
            key={p.id}
            layout
            initial={{ opacity: 0, x: -24 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 24 }}
            transition={{ type: "spring", stiffness: 260, damping: 26 }}
            className={`rounded-xl border p-3 ${
              i === 0
                ? "leader-glow border-accent/40 bg-accent/5"
                : "border-edge bg-surface/60"
            }`}
          >
            <div className="flex items-center gap-3">
              <span
                className={`h-2.5 w-2.5 shrink-0 rounded-full ${i === 0 ? "pulse-dot" : ""}`}
                style={{ background: colorFor(participants, p.id) }}
                aria-hidden
              />
              <span className="truncate font-medium">{p.display_name}</span>
              {p.matched_candidate_id && candidateName(p.matched_candidate_id) && (
                <motion.span
                  layout
                  initial={{ scale: 0.6, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  className="truncate rounded-full border border-accent/40 px-2 py-0.5 text-[10px] tracking-widest text-accent"
                >
                  → {candidateName(p.matched_candidate_id)}
                </motion.span>
              )}
              <AnimatedNumber
                value={p.confidence_score}
                className="ml-auto text-lg font-semibold tabular-nums"
              />
            </div>
            {/* score bar — thin mark, baseline-anchored, springs to width */}
            <div className="mt-2 h-1 overflow-hidden rounded bg-grid">
              <motion.div
                className="h-full rounded"
                style={{ background: colorFor(participants, p.id) }}
                animate={{ width: `${p.confidence_score}%` }}
                transition={{ type: "spring", stiffness: 70, damping: 18 }}
              />
            </div>
            <p className="mt-2 truncate text-xs text-muted" title={p.explanation}>
              {p.explanation}
            </p>
          </motion.div>
        ))}
      </AnimatePresence>

      {interviewers.length > 0 && (
        <p className="pt-1 text-xs text-muted">
          Interviewers (excluded):{" "}
          {interviewers.map((p) => p.display_name).join(", ")}
        </p>
      )}
    </div>
  );
}
