import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";

/**
 * Active Probing banner. Slides in when the backend judges the room
 * unresolvable from passive evidence and drafts a question for the
 * interviewer. TrueCandidate suggests; the human decides.
 */
export default function ProbeBanner({ ambiguity }) {
  const probe = ambiguity?.probe_suggestion;
  const [copied, setCopied] = useState(false);

  return (
    <AnimatePresence>
      {probe && (
        <motion.div
          initial={{ opacity: 0, y: -24, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -12 }}
          transition={{ type: "spring", stiffness: 200, damping: 22 }}
          className="flex items-start gap-3 rounded-xl border border-accent/40 bg-accent/10 p-4 backdrop-blur"
        >
          <motion.span
            className="mt-0.5 text-accent"
            animate={{ scale: [1, 1.25, 1] }}
            transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
            aria-hidden
          >
            ◎
          </motion.span>
          <div className="min-w-0">
            <p className="text-xs uppercase tracking-widest text-accent">
              Active probe suggested — room is ambiguous
            </p>
            <p className="mt-1 text-sm leading-relaxed text-ink">“{probe}”</p>
          </div>
          <button
            onClick={() => {
              navigator.clipboard.writeText(probe);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
            className="ml-auto shrink-0 rounded-lg border border-edge px-3 py-1.5 text-xs text-ink-2 transition-colors hover:bg-surface"
          >
            {copied ? "Copied ✓" : "Copy to chat"}
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
