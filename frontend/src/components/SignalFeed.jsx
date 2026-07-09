import { AnimatePresence, motion } from "framer-motion";
import { colorFor } from "../lib/supabase";

const LABELS = {
  screen_shared: "Initiated Screen Share",
  webcam_enabled: "Turned Webcam On",
  generic_device_name: "Generic Device Name",
  name_matches_candidate: "Name Matches Candidate",
  answered_greeting: "Answered the Greeting",
  intent_to_answer: "First-Person Storytelling",
  asked_interviewer_question: "Asked Interviewer-style Question",
  passive_observer: "Silent Observer (cam off > 3 min)",
  lip_sync_anomaly: "Voice Without Lip Movement",
  narrative_consistent: "Story Stays Consistent",
  narrative_contradiction: "Contradicted Own Story",
  narrative_echo: "Echoed Someone Else's Story",
};

function detail(sig) {
  const p = sig.payload ?? {};
  if (sig.signal_type === "narrative_contradiction")
    return `“${p.new_claim}” vs earlier “${p.contradicts}”`;
  if (sig.signal_type === "narrative_echo")
    return `“${p.claim}” — first said by ${p.first_said_by}`;
  if (sig.signal_type === "narrative_consistent")
    return `“${p.claim}” (claim #${p.ledger_depth})`;
  return p.excerpt ?? p.note ?? null;
}

/**
 * The Explanation Feed — TrueCandidate's reasoning, one event at a time.
 * New events drop in from above with a spring; badges keep icon+text so
 * meaning never rides on color alone.
 */
export default function SignalFeed({ signals, participants }) {
  const byId = Object.fromEntries(participants.map((p) => [p.id, p]));
  const feed = [...signals].reverse().slice(0, 60);

  return (
    <ol className="relative max-h-[480px] space-y-4 overflow-y-auto border-l border-grid pl-5 pr-1">
      <AnimatePresence initial={false}>
        {feed.map((sig) => {
          const who = byId[sig.participant_id];
          const positive = sig.weight >= 0;
          return (
            <motion.li
              key={sig.id}
              layout
              initial={{ opacity: 0, y: -18, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={{ type: "spring", stiffness: 300, damping: 28 }}
              className="relative"
            >
              <motion.span
                className="absolute -left-[26px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-page"
                style={{ background: colorFor(participants, sig.participant_id) }}
                initial={{ scale: 0 }}
                animate={{ scale: 1 }}
                transition={{ type: "spring", stiffness: 500, damping: 20, delay: 0.1 }}
                aria-hidden
              />
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium">
                  {who?.display_name ?? "Unknown"}
                </span>
                <motion.span
                  initial={{ scale: 0.7 }}
                  animate={{ scale: 1 }}
                  transition={{ type: "spring", stiffness: 400, damping: 15 }}
                  className={`rounded-full border px-2 py-0.5 text-xs font-semibold tabular-nums ${
                    positive
                      ? "border-good/50 bg-good/10 text-good"
                      : "border-bad/50 bg-bad/10 text-bad"
                  }`}
                >
                  {positive ? "▲ +" : "▼ "}
                  {sig.weight}: {LABELS[sig.signal_type] ?? sig.signal_type}
                </motion.span>
                <span className="ml-auto text-[11px] text-muted tabular-nums">
                  {new Date(sig.created_at).toLocaleTimeString()}
                </span>
              </div>
              {detail(sig) && (
                <p className="mt-1 text-xs leading-relaxed text-ink-2">
                  {detail(sig)}
                </p>
              )}
            </motion.li>
          );
        })}
      </AnimatePresence>
      {feed.length === 0 && (
        <li className="text-sm text-muted">Waiting for the first signal…</li>
      )}
    </ol>
  );
}
