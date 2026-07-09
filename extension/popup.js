const $ = (id) => document.getElementById(id);
const els = {
  api: $("api"),
  selfName: $("selfName"),
  candidates: $("candidates"),
  interviewers: $("interviewers"),
  toggle: $("toggle"),
  status: $("status"),
  copyRow: $("copyRow"),
  sessionIdBox: $("sessionIdBox"),
  copyBtn: $("copyBtn"),
};

const KEYS = [
  "apiUrl", "sessionId", "observing", "selfName", "candidates", "interviewers",
];

const load = () => new Promise((r) => chrome.storage.local.get(KEYS, r));
const save = (obj) => new Promise((r) => chrome.storage.local.set(obj, r));

function render(cfg) {
  els.api.value = cfg.apiUrl ?? "http://localhost:8000";
  els.selfName.value = cfg.selfName ?? "";
  els.candidates.value = cfg.candidates ?? "";
  els.interviewers.value = cfg.interviewers ?? "";
  els.toggle.textContent = cfg.observing ? "Stop observing" : "Start observing";
  els.toggle.classList.toggle("stop", Boolean(cfg.observing));

  const hasSession = Boolean(cfg.observing && cfg.sessionId);
  els.copyRow.classList.toggle("show", hasSession);
  if (hasSession) els.sessionIdBox.value = cfg.sessionId;

  els.status.innerHTML = hasSession
    ? `<span class="ok">&#9679; Observing.</span> Make sure Meet's captions ` +
      `(CC) are ON. Paste the session id into the dashboard.`
    : "";
}

/**
 * A declared content script only lands in tabs opened AFTER the extension was
 * loaded — an already-open Meet tab has no script at all, which looks exactly
 * like "nothing is happening". Inject it explicitly on every start. The
 * script guards itself against double-injection.
 */
async function ensureInjected() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !/^https:\/\/meet\.google\.com\//.test(tab.url ?? "")) {
    throw new Error("Open this popup from a Google Meet tab.");
  }
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ["content.js"],
  });
}

/** Ask the backend to create a session + roster WITHOUT dispatching any bot. */
async function createSession(apiUrl, candidates, interviewers) {
  const res = await new Promise((resolve) =>
    chrome.runtime.sendMessage(
      {
        type: "truecandidate:post",
        apiUrl,
        path: "/sessions/launch",
        body: {
          meeting_url: "https://meet.google.com/extension",
          candidates,
          interviewers,
          dispatch_bot: false, // the extension IS the capture layer
        },
      },
      resolve,
    ),
  );
  if (!res?.ok) throw new Error(res?.error ?? res?.text ?? "backend unreachable");
  if (res.data?.error) throw new Error(res.data.error);
  return res.data.session_id;
}

els.toggle.addEventListener("click", async () => {
  const cfg = await load();

  if (cfg.observing) {
    await save({ observing: false });
    render(await load());
    return;
  }

  const apiUrl = els.api.value.trim() || "http://localhost:8000";
  const selfName = els.selfName.value.trim();
  const split = (v) => v.split(",").map((s) => s.trim()).filter(Boolean);
  const candidates = split(els.candidates.value);
  const interviewers = split(els.interviewers.value);

  if (!candidates.length) {
    els.status.innerHTML = `<span class="err">Name at least one expected candidate.</span>`;
    return;
  }
  // Without this, Meet's "You" caption label becomes a participant named "You"
  // and can never match a roster candidate.
  if (!selfName) {
    els.status.innerHTML =
      `<span class="err">Enter your own Meet name — TrueCandidate needs it ` +
      `to resolve the "You" caption label.</span>`;
    return;
  }

  els.toggle.disabled = true;
  els.status.textContent = "Starting...";
  try {
    await ensureInjected();
    const sessionId = await createSession(apiUrl, candidates, interviewers);
    await save({
      apiUrl,
      sessionId,
      selfName,
      observing: true,
      candidates: els.candidates.value,
      interviewers: els.interviewers.value,
    });
    render(await load());
  } catch (e) {
    els.status.innerHTML = `<span class="err">${e.message}</span>`;
  } finally {
    els.toggle.disabled = false;
  }
});

els.copyBtn.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(els.sessionIdBox.value);
  } catch {
    // Clipboard API can be blocked in some contexts; fall back to a manual
    // select so the user can still Ctrl+C.
    els.sessionIdBox.select();
    document.execCommand("copy");
  }
  els.copyBtn.textContent = "Copied!";
  els.copyBtn.classList.add("copied");
  setTimeout(() => {
    els.copyBtn.textContent = "Copy";
    els.copyBtn.classList.remove("copied");
  }, 1500);
});

load().then(render);
