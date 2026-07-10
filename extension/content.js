/**
 * TrueCandidate content script — runs inside the interviewer's own Google Meet tab.
 *
 * WHY this exists: a meeting *bot* has to be admitted, and Google blocks
 * anonymous guests from many meetings ("You can't join this video call"), so
 * the bot needs its own Google account. That is friction the interviewer
 * shouldn't have to accept. This extension needs none of it — it reads the
 * captions already rendered in the tab you're sitting in. No extra
 * participant, no admission, no account.
 *
 * Meet's captions are speaker-attributed, so this yields exactly the two
 * inputs the backend ensemble needs: WHO spoke and WHAT they said.
 *
 * DOM FRAGILITY: Google reshuffles Meet's markup regularly, so we never bet on
 * one selector. `findCaptionRoot()` tries several strategies in order. If all
 * miss, the badge says so and `window.__trueCandidateDebug()` dumps what we see —
 * that is the one function to run when captions stop flowing.
 *
 * This file is BOTH a declared content script and injected on demand by the
 * popup (a declared script never lands in tabs that were already open when the
 * extension loaded). The guard below makes double-injection a no-op.
 */
(() => {
  // Reloading the extension ORPHANS any content script already in the page:
  // its `chrome.runtime` becomes undefined, but its timer keeps firing and
  // every POST throws. So a fresh instance shuts the previous one down and
  // takes over, rather than politely returning.
  if (typeof window.__trueCandidateStop === "function") {
    try {
      window.__trueCandidateStop();
    } catch {
      /* the old instance may already be dead */
    }
  }

  const POLL_MS = 600;
  const FINALIZE_AFTER_MS = 2000; // caption text stable this long => final
  const WPM_MS = 400; // rough per-word duration
  const MAX_ROOT_TEXT = 2000; // reject a "caption root" that is really the app shell
  const SCAN_CAP = 250; // max elements the fallback scan will touch per tick

  const state = {
    apiUrl: null,
    sessionId: null,
    selfName: null, // what to call the "You" speaker
    knownNames: new Set(), // candidates + interviewers + self, lowercased
    observing: false,
    buffer: new Map(), // speaker -> { text, changedAt }
    posted: new Map(), // speaker -> full text already sent
    presenter: null,
    chunks: 0,
    timer: null,
    rootStrategy: null,
    missTicks: 0,
    lastError: null,
    stopped: null,
  };

  /** Stable per-name participant id (the backend keys participants on this). */
  function pid(name) {
    let h = 5381;
    for (let i = 0; i < name.length; i++) h = ((h << 5) + h + name.charCodeAt(i)) | 0;
    return `meet-${Math.abs(h)}`;
  }

  /** True while this script can still talk to its extension. Goes false the
   *  moment the extension is reloaded/updated — the classic "Extension context
   *  invalidated" state, where `chrome.runtime` is gone but our timer lives on. */
  function contextAlive() {
    try {
      return Boolean(chrome.runtime?.id);
    } catch {
      return false;
    }
  }

  function post(path, body) {
    if (!contextAlive()) {
      stop("context-invalidated");
      return Promise.resolve({ ok: false, error: "extension context invalidated" });
    }
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(
          { type: "truecandidate:post", apiUrl: state.apiUrl, path, body },
          (res) => resolve(res ?? { ok: false, error: chrome.runtime.lastError?.message }),
        );
      } catch (e) {
        stop("context-invalidated");
        resolve({ ok: false, error: String(e) });
      }
    });
  }

  // -------------------------------------------------------------------------
  // Caption root discovery — ordered strategies, most specific first.
  //
  // A candidate only WINS if its text actually parses into speaker/text pairs.
  // Checking "has some text" is not enough: Meet has decoy elements (a bare
  // "Captions" label, a jsname wrapper) that match a selector, contain text,
  // and yield nothing — the scraper then sits at 0 chunks forever without a
  // single error. Parseability is the real test, so that is what we test.
  // -------------------------------------------------------------------------
  const STRATEGIES = [
    ["jsname", () => document.querySelector('div[jsname="dsyhDe"]')],
    ["class-a4cQT", () => document.querySelector("div.a4cQT")],
    [
      "aria-region",
      () => document.querySelector('div[role="region"][aria-label*="aption" i]'),
    ],
    [
      "aria-live-richest",
      () => {
        // The caption container is an aria-live region. Several exist (toasts,
        // status bars); pick the one carrying the most text.
        const lives = [...document.querySelectorAll("[aria-live]")].filter(
          (el) => (el.innerText || "").trim().length > 0,
        );
        if (!lives.length) return null;
        return lives.sort(
          (a, b) => (b.innerText || "").length - (a.innerText || "").length,
        )[0];
      },
    ],
    [
      "scan-parseable",
      () => {
        // Last resort: sweep the plausible containers and return the first one
        // whose text genuinely parses as captions. Survives Meet renaming its
        // classes and jsnames, which it does regularly.
        //
        // Bounded on purpose: this runs every tick until a root is found, and
        // `innerText` forces layout. Gate on `textContent` (no layout) first,
        // and cap how many elements we are willing to touch.
        const candidates = document.querySelectorAll(
          '[aria-live], div[jsname], div[role="region"]',
        );
        let examined = 0;
        for (const el of candidates) {
          if (examined >= SCAN_CAP) break;
          const rough = el.textContent || "";
          if (!rough.trim() || rough.length > MAX_ROOT_TEXT) continue;
          examined += 1;
          if (looksLikeCaptions(parseLines(el.innerText))) return el;
        }
        return null;
      },
    ],
  ];

  /** Cheap size gate: a caption box holds a few lines, never the whole app shell. */
  function sizeOk(el) {
    const text = (el?.innerText || "").trim();
    return Boolean(text) && text.length <= MAX_ROOT_TEXT;
  }

  /**
   * The real test: does this element yield pairs that actually look like
   * captions? Merely yielding a pair is not enough — a two-line UI decoy such
   * as "Captions / Settings" parses into one pair and would otherwise win.
   * A genuine pair names a speaker we know, or carries sentence-like text.
   */
  function looksLikeCaptions(entries) {
    return entries.some(
      ([who, text]) =>
        who === "You" ||
        state.knownNames.has(who.toLowerCase()) ||
        text.split(/\s+/).length >= 3,
    );
  }

  function isPlausibleRoot(el) {
    return sizeOk(el) && looksLikeCaptions(parseLines(el.innerText));
  }

  let cachedRoot = null;
  function findCaptionRoot() {
    if (cachedRoot && document.contains(cachedRoot) && isPlausibleRoot(cachedRoot)) {
      return cachedRoot;
    }
    for (const [name, fn] of STRATEGIES) {
      let el = null;
      try {
        el = fn();
      } catch {
        /* strategy threw on a weird DOM — try the next one */
      }
      if (isPlausibleRoot(el)) {
        if (state.rootStrategy !== name) {
          state.rootStrategy = name;
          console.info(
            `[TrueCandidate] caption root found via "${name}" — ` +
              `${parseLines(el.innerText).length} caption line(s) parsed`,
          );
        }
        cachedRoot = el;
        return el;
      }
    }
    cachedRoot = null;
    state.rootStrategy = null;
    return null;
  }

  function captionText() {
    const root = findCaptionRoot();
    return root ? root.innerText : null;
  }

  /**
   * Meet renders a speaker name on its own line, then that speaker's text.
   *
   * The names typed into the popup (candidates, interviewers, your own) are the
   * strongest evidence available, so check those first. The shape heuristic is
   * only the fallback — on its own it happily mistakes a short caption line
   * like "Guys are here" for a speaker name and splits the transcript wrong.
   */
  function isSpeakerLine(line) {
    if (line === "You" || state.knownNames.has(line.toLowerCase())) return true;
    return (
      line.length <= 40 &&
      !/[.!?,]$/.test(line) &&
      line.split(/\s+/).length <= 4 &&
      line[0] === line[0].toUpperCase()
    );
  }

  function parseLines(raw) {
    const lines = raw.split("\n").map((l) => l.trim()).filter(Boolean);
    const out = [];
    let current = null;
    let buf = [];
    for (const line of lines) {
      const looksLikeName = isSpeakerLine(line);
      if (looksLikeName && (current === null || buf.length)) {
        if (current && buf.length) out.push([current, buf.join(" ")]);
        current = line;
        buf = [];
      } else {
        buf.push(line);
      }
    }
    if (current && buf.length) out.push([current, buf.join(" ")]);
    return out;
  }

  /**
   * Meet labels the local user's captions "You". Map that to the name the
   * interviewer gave us, so their turns still feed the greeting detector —
   * dropping them silently used to make a solo test look completely dead.
   */
  function resolveSpeaker(raw) {
    return raw === "You" ? state.selfName || "You" : raw;
  }

  /**
   * Meet's caption region is a ROLLING transcript: it keeps appending to the
   * same speaker's line. Posting the whole line every time would re-send text
   * the backend already scored (and re-trigger narrative-ledger claims). Send
   * only what is new.
   */
  function deltaText(prev, next) {
    if (!prev) return next;
    if (next.startsWith(prev)) return next.slice(prev.length).trim();
    return next; // region scrolled/reset — treat as fresh
  }

  /** Count a chunk only once the backend actually accepted it — the badge must
   *  never claim progress that never left the browser. */
  async function emitChunk(speaker, text) {
    const res = await post("/webhook/transcript", {
      session_id: state.sessionId,
      platform_participant_id: pid(speaker),
      display_name: speaker,
      text,
      started_at_ms: 0,
      duration_ms: text.split(/\s+/).length * WPM_MS,
    });
    if (res?.ok) {
      state.chunks += 1;
      state.lastError = null;
    } else {
      state.lastError = res?.error ?? `HTTP ${res?.status}`;
      console.warn("[TrueCandidate] transcript POST failed:", state.lastError, res);
    }
    paintBadge();
  }

  /** Buffer partial captions; emit only once the ASR stops rewriting them. */
  async function tick() {
    if (!state.observing || state.stopped) return;
    if (!contextAlive()) return stop("context-invalidated");

    const raw = captionText();
    const now = Date.now();
    const seen = new Set();

    if (raw) {
      state.missTicks = 0;
      for (const [rawSpeaker, text] of parseLines(raw)) {
        const speaker = resolveSpeaker(rawSpeaker);
        seen.add(speaker);
        const prev = state.buffer.get(speaker);
        if (!prev || prev.text !== text) {
          state.buffer.set(speaker, { text, changedAt: now });
        }
      }
    } else {
      state.missTicks += 1;
      // Dump the diagnostic automatically. `window.__trueCandidateDebug` lives
      // in the content script's ISOLATED world, so the page console cannot see
      // it unless you switch the DevTools context — don't make the user find
      // that out the hard way when the scraper is already failing.
      if (state.missTicks === 25) {
        console.warn(
          "[TrueCandidate] No parseable caption container after ~15s. " +
            "Turn on captions (CC) in Meet and have someone speak. If captions " +
            "ARE visible, Google changed the DOM — the report below shows what " +
            "each discovery strategy sees; patch findCaptionRoot() accordingly.",
        );
        try {
          console.warn("[TrueCandidate] diagnostic", window.__trueCandidateDebug());
        } catch {
          /* never let diagnostics break the scraper */
        }
      }
    }

    for (const [speaker, { text, changedAt }] of [...state.buffer]) {
      if (now - changedAt >= FINALIZE_AFTER_MS || !seen.has(speaker)) {
        state.buffer.delete(speaker);
        const fresh = deltaText(state.posted.get(speaker), text);
        if (fresh) {
          state.posted.set(speaker, text);
          await emitChunk(speaker, fresh);
        }
      }
    }

    // Screen share: Meet surfaces "<name> is presenting" in the UI.
    const body = document.body.innerText.slice(0, 4000);
    const m = body.match(/^(.{2,40}?) is presenting$/m);
    const current = m ? m[1].trim() : null;
    if (current && current !== state.presenter && current !== "You") {
      state.presenter = current;
      await post("/webhook/events", {
        session_id: state.sessionId,
        platform_participant_id: pid(current),
        display_name: current,
        event: "screen_share_started",
        payload: {},
      });
    } else if (!current) {
      state.presenter = null;
    }
  }

  // -------------------------------------------------------------------------
  // Diagnostics — the thing to run when captions stop flowing.
  // -------------------------------------------------------------------------
  window.__trueCandidateDebug = () => {
    const report = {
      observing: state.observing,
      sessionId: state.sessionId,
      apiUrl: state.apiUrl,
      selfName: state.selfName,
      chunksPosted: state.chunks,
      rootStrategy: state.rootStrategy,
      rootFound: Boolean(findCaptionRoot()),
      strategies: STRATEGIES.map(([name, fn]) => {
        let hit = null;
        try {
          hit = fn();
        } catch {
          /* ignore */
        }
        return {
          name,
          matched: Boolean(hit),
          textLength: hit ? (hit.innerText || "").length : 0,
          textPreview: hit ? (hit.innerText || "").slice(0, 120) : null,
        };
      }),
      ariaLiveElements: [...document.querySelectorAll("[aria-live]")].map((el) => ({
        ariaLive: el.getAttribute("aria-live"),
        ariaLabel: el.getAttribute("aria-label"),
        className: el.className,
        jsname: el.getAttribute("jsname"),
        textPreview: (el.innerText || "").slice(0, 120),
      })),
    };
    console.log("[TrueCandidate] debug report", report);
    const root = findCaptionRoot();
    if (root) console.log("[TrueCandidate] parsed entries", parseLines(root.innerText));
    return report;
  };

  // -------------------------------------------------------------------------
  // Floating status badge, so it is always obvious the extension is recording.
  // -------------------------------------------------------------------------
  let badge;
  function paintBadge() {
    if (!badge) {
      badge = document.createElement("div");
      badge.style.cssText = [
        "position:fixed", "left:16px", "bottom:16px", "z-index:2147483647",
        "background:rgba(14,22,38,.92)", "color:#f4f6fb", "font:500 12px system-ui",
        "padding:8px 12px", "border-radius:999px",
        "border:1px solid rgba(255,255,255,.14)",
        "backdrop-filter:blur(8px)", "pointer-events:none",
      ].join(";");
      document.body.appendChild(badge);
    }

    let dot = "#7c8699";
    let label = "TrueCandidate idle";
    if (state.stopped === "context-invalidated") {
      dot = "#d03b3b";
      label = "TrueCandidate: extension reloaded — refresh this tab";
    } else if (state.lastError) {
      dot = "#d03b3b";
      label = `TrueCandidate: backend unreachable (${state.chunks} sent)`;
    } else if (state.observing) {
      const captionsMissing = state.missTicks > 5;
      dot = captionsMissing ? "#fab219" : "#0ca30c";
      label = captionsMissing
        ? "TrueCandidate: turn on captions (CC)"
        : `TrueCandidate observing · ${state.chunks} chunks`;
    }
    badge.innerHTML =
      `<span style="display:inline-block;width:7px;height:7px;border-radius:50%;` +
      `background:${dot};margin-right:7px"></span>${label}`;
  }

  /** Shut this instance down for good: kill the timer so a stale script can't
   *  keep polling, and say why on the badge. */
  function stop(reason) {
    if (state.stopped) return;
    state.stopped = reason ?? "stopped";
    state.observing = false;
    clearInterval(state.timer);
    state.timer = null;
    if (reason === "replaced") {
      badge?.remove(); // the incoming instance paints its own
      badge = null;
      return;
    }
    if (reason === "context-invalidated") {
      console.warn(
        "[TrueCandidate] extension context invalidated (the extension was " +
          "reloaded). This script is now inert — refresh the Meet tab.",
      );
      try {
        paintBadge();
      } catch {
        /* page may be tearing down */
      }
    }
  }

  // -------------------------------------------------------------------------
  // Wiring: popup writes config to storage; we react to it.
  // -------------------------------------------------------------------------
  function applyConfig(cfg) {
    if (state.stopped) return;
    state.apiUrl = cfg.apiUrl ?? state.apiUrl;
    state.sessionId = cfg.sessionId ?? state.sessionId;
    state.selfName = (cfg.selfName ?? "").trim() || state.selfName;
    state.observing = Boolean(cfg.observing && state.apiUrl && state.sessionId);

    // Names we can trust as speaker labels when parsing the caption block.
    state.knownNames = new Set(
      [cfg.selfName, ...(cfg.candidates ?? "").split(","),
       ...(cfg.interviewers ?? "").split(",")]
        .map((n) => (n ?? "").trim().toLowerCase())
        .filter(Boolean),
    );

    state.missTicks = 0;
    state.lastError = null;
    paintBadge();

    clearInterval(state.timer);
    if (state.observing) {
      if (!state.selfName) {
        console.warn(
          '[TrueCandidate] "Your name as it appears in Meet" is unset, so your own ' +
            'captions will be attributed to a participant literally named "You".',
        );
      }
      console.info(
        `[TrueCandidate] observing session ${state.sessionId} → ${state.apiUrl} ` +
          `(you = ${state.selfName || "unset"}). ` +
          `Run window.__trueCandidateDebug() if nothing appears.`,
      );
      state.timer = setInterval(() => {
        tick().catch((e) => console.warn("[TrueCandidate] tick failed", e));
        paintBadge();
      }, POLL_MS);
    }
  }

  // Let a future injection shut this instance down cleanly (see top of file).
  window.__trueCandidateStop = () => stop("replaced");

  const KEYS = [
    "apiUrl", "sessionId", "observing", "selfName", "candidates", "interviewers",
  ];
  chrome.storage.local.get(KEYS, applyConfig);
  chrome.storage.onChanged.addListener(() => {
    if (!contextAlive()) return stop("context-invalidated");
    chrome.storage.local.get(KEYS, applyConfig);
  });
})();
