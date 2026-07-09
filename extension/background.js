/**
 * Service worker: the network relay.
 *
 * WHY relay instead of fetching from the content script: a content script
 * inherits meet.google.com's origin, so every POST to your backend would be a
 * cross-origin request subject to Meet's CSP. The service worker holds the
 * extension's `host_permissions`, so its fetches are unrestricted — the
 * content script just posts messages to it.
 */
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type !== "truecandidate:post") return false;

  fetch(`${msg.apiUrl.replace(/\/$/, "")}${msg.path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(msg.body),
  })
    .then(async (r) => {
      const text = await r.text();
      let data = null;
      try { data = JSON.parse(text); } catch { /* non-JSON error body */ }
      sendResponse({ ok: r.ok, status: r.status, data, text });
    })
    .catch((err) => sendResponse({ ok: false, error: String(err) }));

  return true; // keep the message channel open for the async response
});
