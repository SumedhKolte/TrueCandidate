import { motion } from "framer-motion";
import { useState } from "react";
import Logo from "./Logo";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

/**
 * The real-life entry point: paste a Google Meet / Zoom / Teams link plus the
 * expected candidate names, and TrueCandidate dispatches a meeting bot that
 * joins the call and starts streaming evidence. On success we jump straight
 * into the live dashboard for the new session.
 */
export default function LaunchPanel({ onLaunched }) {
  const [meetingUrl, setMeetingUrl] = useState("");
  const [candidates, setCandidates] = useState("");
  const [interviewers, setInterviewers] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const launch = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API}/sessions/launch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meeting_url: meetingUrl.trim(),
          candidates: candidates.split(",").map((s) => s.trim()).filter(Boolean),
          interviewers: interviewers.split(",").map((s) => s.trim()).filter(Boolean),
        }),
      });
      const data = await res.json();
      if (!res.ok || data.error || data.detail) {
        throw new Error(data.error ?? data.detail ?? `HTTP ${res.status}`);
      }
      onLaunched(data.session_id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <motion.form
      onSubmit={launch}
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: "spring", stiffness: 120, damping: 20 }}
      className="panel mx-auto mt-10 max-w-xl space-y-4 p-8"
    >
      <div>
        <Logo className="mb-4 h-9" />
        <h2 className="text-lg font-semibold">Observe a live meeting</h2>
        <p className="mt-1 text-sm text-ink-2">
          Paste the meeting link and who you expect in the room. A bot named{" "}
          <span className="text-ink">"TrueCandidate Observer"</span> will join
          and start verifying — announce it to participants (recording consent).
        </p>
      </div>

      <label className="block text-xs uppercase tracking-widest text-muted">
        Meeting link
        <input
          required
          value={meetingUrl}
          onChange={(e) => setMeetingUrl(e.target.value)}
          placeholder="https://meet.google.com/abc-defg-hij"
          className="mt-1.5 w-full rounded-lg border border-edge bg-surface/70 px-3 py-2.5 text-sm normal-case tracking-normal text-ink outline-none placeholder:text-muted focus:border-accent/60"
        />
      </label>

      <label className="block text-xs uppercase tracking-widest text-muted">
        Expected candidates (comma-separated)
        <input
          required
          value={candidates}
          onChange={(e) => setCandidates(e.target.value)}
          placeholder="Priya Sharma, Arjun Mehta"
          className="mt-1.5 w-full rounded-lg border border-edge bg-surface/70 px-3 py-2.5 text-sm normal-case tracking-normal text-ink outline-none placeholder:text-muted focus:border-accent/60"
        />
      </label>

      <label className="block text-xs uppercase tracking-widest text-muted">
        Interviewers (comma-separated, optional)
        <input
          value={interviewers}
          onChange={(e) => setInterviewers(e.target.value)}
          placeholder="Rachel Kim"
          className="mt-1.5 w-full rounded-lg border border-edge bg-surface/70 px-3 py-2.5 text-sm normal-case tracking-normal text-ink outline-none placeholder:text-muted focus:border-accent/60"
        />
      </label>

      {error && (
        <p className="rounded-lg border border-bad/40 bg-bad/10 px-3 py-2 text-xs text-bad">
          {error}
        </p>
      )}

      <motion.button
        whileHover={{ scale: busy ? 1 : 1.02 }}
        whileTap={{ scale: busy ? 1 : 0.98 }}
        disabled={busy}
        className="w-full rounded-lg bg-accent px-4 py-3 text-sm font-medium text-white disabled:opacity-60"
      >
        {busy ? "Dispatching bot…" : "Send TrueCandidate to this meeting"}
      </motion.button>

      <p className="text-center text-[11px] text-muted">
        Already have a session id? Paste it in the header field instead.
      </p>
    </motion.form>
  );
}
