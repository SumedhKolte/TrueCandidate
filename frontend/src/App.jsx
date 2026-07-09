import { motion } from "framer-motion";
import { useState } from "react";
import CandidateRoster from "./components/CandidateRoster";
import ConfidenceGauge from "./components/ConfidenceGauge";
import LaunchPanel from "./components/LaunchPanel";
import Leaderboard from "./components/Leaderboard";
import Logo from "./components/Logo";
import ProbeBanner from "./components/ProbeBanner";
import ScoreTimeline from "./components/ScoreTimeline";
import SignalFeed from "./components/SignalFeed";
import { useTrueCandidateSession } from "./hooks/useTrueCandidateSession";
import { colorFor } from "./lib/supabase";

const stagger = {
  hidden: { opacity: 0, y: 24 },
  show: (i) => ({
    opacity: 1,
    y: 0,
    transition: { type: "spring", stiffness: 120, damping: 20, delay: i * 0.08 },
  }),
};

function Panel({ title, children, className = "", index = 0 }) {
  return (
    <motion.section
      custom={index}
      variants={stagger}
      initial="hidden"
      animate="show"
      className={`panel p-5 ${className}`}
    >
      <h2 className="mb-4 text-[11px] font-medium uppercase tracking-[0.2em] text-muted">
        {title}
      </h2>
      {children}
    </motion.section>
  );
}

export default function App() {
  const [sessionId, setSessionId] = useState(
    new URLSearchParams(window.location.search).get("session") ?? "",
  );
  const [input, setInput] = useState(sessionId);
  const { session, participants, signals, timeline, candidates } =
    useTrueCandidateSession(sessionId);

  const leader = [...participants]
    .filter((p) => !p.is_interviewer)
    .sort((a, b) => b.confidence_score - a.confidence_score)[0];

  return (
    <>
      <div className="ambient" aria-hidden />
      <div className="grid-overlay" aria-hidden />

      <div className="mx-auto min-h-screen max-w-7xl px-6 py-8">
        <motion.header
          initial={{ opacity: 0, y: -16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: "spring", stiffness: 100, damping: 18 }}
          className="mb-8 flex flex-wrap items-center gap-4"
        >
          <div>
            <h1 className="flex items-center gap-3 text-xl font-semibold tracking-tight">
              <Logo markOnly className="h-7 w-7" />
              TrueCandidate <span className="font-normal text-muted">/ Live Observer</span>
              {session?.status === "live" && (
                <span className="flex items-center gap-2 text-xs font-normal text-good">
                  <span className="pulse-dot h-2 w-2 rounded-full bg-good" />
                  LIVE
                </span>
              )}
            </h1>
            {session && (
              <p className="mt-0.5 text-sm text-ink-2">
                Verifying{" "}
                <span className="text-ink">
                  {candidates.map((c) => c.candidate_name).join(", ") ||
                    session.candidate_name}
                </span>{" "}
                on {session.platform} ·{" "}
                <span className="tabular-nums text-good">
                  {candidates.filter((c) => c.status === "verified").length}
                </span>
                /{candidates.length} verified
              </p>
            )}
          </div>
          <form
            className="ml-auto flex gap-2"
            onSubmit={(e) => { e.preventDefault(); setSessionId(input.trim()); }}
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="session id…"
              className="w-72 rounded-lg border border-edge bg-surface/70 px-3 py-2 text-sm outline-none backdrop-blur placeholder:text-muted focus:border-accent/60"
            />
            <motion.button
              whileHover={{ scale: 1.03 }}
              whileTap={{ scale: 0.97 }}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white"
            >
              Observe
            </motion.button>
          </form>
        </motion.header>

        {!sessionId ? (
          <LaunchPanel
            onLaunched={(id) => {
              setInput(id);
              setSessionId(id);
            }}
          />
        ) : (
          <div className="space-y-6">
            <ProbeBanner ambiguity={session?.ambiguity} />

            <CandidateRoster candidates={candidates} participants={participants} />

            <div className="grid gap-6 lg:grid-cols-3">
              <Panel title="Identification Confidence" index={0}>
                <ConfidenceGauge
                  score={leader?.confidence_score ?? 50}
                  name={leader?.display_name}
                  color={leader ? colorFor(participants, leader.id) : undefined}
                />
                {session?.ambiguity?.entropy != null && (
                  <p className="mt-4 text-center text-xs text-muted">
                    Room ambiguity (entropy):{" "}
                    <span className="text-ink-2 tabular-nums">
                      {session.ambiguity.entropy}
                    </span>
                  </p>
                )}
              </Panel>

              <Panel title="Confidence Over Time" className="lg:col-span-2" index={1}>
                <ScoreTimeline timeline={timeline} participants={participants} />
              </Panel>
            </div>

            <div className="grid gap-6 lg:grid-cols-3">
              <Panel title="Participants" index={2}>
                <Leaderboard participants={participants} candidates={candidates} />
              </Panel>
              <Panel
                title="Explanation Feed — why the score moved"
                className="lg:col-span-2"
                index={3}
              >
                <SignalFeed signals={signals} participants={participants} />
              </Panel>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
