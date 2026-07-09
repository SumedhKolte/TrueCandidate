"""
Self-hosted Google Meet bot — no third-party API, no business email.

How it works: Playwright drives a real Chromium that joins the Meet as a
guest named "TrueCandidate Observer" (mic/cam off), turns on live captions, and
scrapes them from the DOM. Google Meet's captions are ALREADY speaker-
attributed, so we get transcript + speaker identity from Google's own
production ASR for free — the exact two things the signal pipelines need.
Chunks are POSTed to the normal /webhook/transcript endpoint, so the whole
ensemble (greeting mapper, Groq intent, narrative ledger…) runs unchanged.

Run standalone:
    python -m bot.meet_bot --meet-url https://meet.google.com/xxx-yyyy-zzz \
                           --session-id <uuid> [--api http://localhost:8000] [--headless]

Resilience notes (learned the hard way):
  * DO NOT close the Chromium window — that IS the bot. Closing it makes the
    participant leave.
  * Run uvicorn WITHOUT --reload during live sessions; a reload restarts the
    console process group and can take the bot down with it (the launcher
    also detaches the bot into its own console to defend against this).
  * The bot retries the whole join up to MAX_ATTEMPTS times if the browser
    dies or the page closes mid-wait.
  * Google changes Meet's DOM regularly. Caption parsing tries a structured
    region first, then falls back to innerText pair-parsing; _CAPTION_JS is
    the single place to patch if captions stop flowing.
  * Consent: announce the bot — analyzing a call requires participant consent.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import random
import re
import time

import httpx

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s meetbot %(levelname)s %(message)s")
log = logging.getLogger("meetbot")

BOT_NAME = "TrueCandidate Observer"
POLL_S = 0.6              # caption poll cadence
FINALIZE_AFTER_S = 2.0    # caption text unchanged this long => chunk is final
WPM_MS = 400              # rough per-word speaking duration for duration_ms
ADMIT_TIMEOUT_S = 300     # how long we wait at the knock
MAX_ATTEMPTS = 3          # full rejoin attempts if the browser/page dies
KEEPALIVE_S = 25          # mouse wiggle so Meet never thinks we're idle

# Any of these means "we are inside the call" — Meet A/B-tests its UI, so we
# check several signals instead of betting on one aria-label.
_IN_CALL_SELECTORS = [
    '[aria-label*="Leave call" i]',
    'button[jsname="CQylAd"]',            # historical leave-button jsname
    '[aria-label*="Turn on captions" i]',
    '[aria-label*="Turn off captions" i]',
    '[aria-label*="People" i][role="button"]',
]
# Any of these on the page means the join failed / call ended.
_DEAD_TEXTS = [
    "You can't join this video call",
    "denied your request",
    "You've been removed",
    "Return to home screen",
    "The meeting hasn't started",
    "You left the meeting",
]

# Ordered discovery strategies, most specific first. Google reshuffles Meet's
# markup, so never bet on one selector. Mirror of findCaptionRoot() in the
# Chrome extension (extension/content.js) — patch both together.
_CAPTION_JS = """
() => {
  const strategies = [
    () => document.querySelector('div[jsname="dsyhDe"]'),
    () => document.querySelector('div.a4cQT'),
    () => document.querySelector('div[role="region"][aria-label*="aption" i]'),
    () => {
      const lives = [...document.querySelectorAll('[aria-live]')]
        .filter(el => (el.innerText || '').trim().length > 0);
      if (!lives.length) return null;
      return lives.sort((a, b) =>
        (b.innerText || '').length - (a.innerText || '').length)[0];
    },
  ];
  for (const fn of strategies) {
    let el = null;
    try { el = fn(); } catch (e) { /* try next */ }
    if (el && (el.innerText || '').trim()) return el.innerText;
  }
  return null;
}
"""

_PRESENTING_RE = re.compile(r"^(.{2,40}?) is presenting$", re.MULTILINE)


class FatalJoinError(RuntimeError):
    """A join failure that retrying cannot fix (e.g. sign-in required)."""


class CaptionTracker:
    """Turns a stream of (speaker, partial-text) polls into finalized chunks.

    Meet rewrites the current caption line as ASR refines it, so we buffer per
    speaker and emit only when the text stopped changing for FINALIZE_AFTER_S
    (or the speaker's line disappeared = they yielded the floor)."""

    def __init__(self, post):
        self._post = post
        self._buf: dict[str, tuple[str, float]] = {}
        self._posted: dict[str, str] = {}

    async def update(self, entries: list[tuple[str, str]]) -> None:
        now = time.monotonic()
        seen = set()
        for speaker, text in entries:
            seen.add(speaker)
            prev = self._buf.get(speaker)
            if prev is None or prev[0] != text:
                self._buf[speaker] = (text, now)

        for speaker in list(self._buf):
            text, changed_at = self._buf[speaker]
            if (now - changed_at) >= FINALIZE_AFTER_S or speaker not in seen:
                del self._buf[speaker]
                if text and text != self._posted.get(speaker):
                    self._posted[speaker] = text
                    await self._post(speaker, text)


def _parse_lines(raw: str) -> list[tuple[str, str]]:
    """Pair innerText lines as (speaker, text): Meet renders the speaker name
    on its own line followed by that speaker's caption text line(s)."""
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    out: list[tuple[str, str]] = []
    current: str | None = None
    buf: list[str] = []
    for line in lines:
        looks_like_name = (
            len(line) <= 40
            and not re.search(r"[.!?,]$", line)
            and len(line.split()) <= 4
            and (line[:1].isupper() or line == "You")
        )
        if looks_like_name and (current is None or buf):
            if current and buf:
                out.append((current, " ".join(buf)))
            current, buf = line, []
        else:
            buf.append(line)
    if current and buf:
        out.append((current, " ".join(buf)))
    return out


async def _first_visible(page, selectors: list[str]) -> bool:
    for sel in selectors:
        try:
            if await page.locator(sel).first.is_visible(timeout=200):
                return True
        except Exception:
            continue
    return False


async def _page_has_dead_text(page) -> str | None:
    try:
        body = await page.evaluate("() => document.body.innerText.slice(0, 5000)")
    except Exception:
        return "page unreachable"
    for t in _DEAD_TEXTS:
        if t.lower() in (body or "").lower():
            return t
    return None


async def _join(page) -> None:
    """Drive the pre-join screen: guest name, mic/cam off, ask to join,
    then poll (rather than a single brittle wait_for) until we're in-call."""
    await asyncio.sleep(3)

    # Dismiss occasional consent/"Got it" popups
    for label in ("Got it", "Dismiss", "Continue without microphone and camera"):
        btn = page.get_by_role("button", name=label)
        try:
            if await btn.count():
                await btn.first.click(timeout=1500)
        except Exception:
            pass

    name_box = page.locator('input[aria-label*="name" i], input[placeholder*="name" i]')
    if await name_box.count():
        await name_box.first.fill(BOT_NAME)
        log.info("entered guest name %r", BOT_NAME)

    for label in ("Turn off microphone", "Turn off camera"):
        btn = page.locator(f'[aria-label*="{label}" i]')
        try:
            if await btn.count():
                await btn.first.click(timeout=2000)
        except Exception:
            pass

    clicked = None
    for label in ("Ask to join", "Join now", "Join anyway"):
        btn = page.get_by_role("button", name=label)
        if await btn.count():
            await btn.first.click()
            clicked = label
            break
    if not clicked:
        raise RuntimeError("no join button found — Meet UI changed or link invalid")
    log.info("clicked %r — waiting for the host to admit (up to %ds)…",
             clicked, ADMIT_TIMEOUT_S)

    deadline = time.monotonic() + ADMIT_TIMEOUT_S
    while time.monotonic() < deadline:
        if await _first_visible(page, _IN_CALL_SELECTORS):
            log.info("admitted to the meeting ✓")
            return
        dead = await _page_has_dead_text(page)
        if dead:
            if "can't join" in dead.lower():
                raise FatalJoinError(
                    "Meet refused an anonymous guest. This meeting requires a "
                    "signed-in Google account. Fix: run `python -m bot.login` "
                    "once, set MEET_BOT_PROFILE_DIR in backend/.env — or skip "
                    "the bot entirely and use the Chrome extension in "
                    "extension/ (no bot, no account, no admission)."
                )
            raise RuntimeError(f"join failed: {dead!r}")
        # A second "Join now" screen sometimes appears after admission
        btn = page.get_by_role("button", name="Join now")
        try:
            if await btn.count():
                await btn.first.click(timeout=1500)
        except Exception:
            pass
        await asyncio.sleep(2)
    raise RuntimeError("host never admitted the bot within the wait window")


async def _enable_captions(page) -> None:
    try:
        await page.keyboard.press("c")
    except Exception:
        pass
    btn = page.locator('[aria-label*="Turn on captions" i]')
    try:
        if await btn.count():
            await btn.first.click(timeout=2000)
    except Exception:
        pass
    log.info("captions requested — scraping…")


async def _scrape(page, tracker: CaptionTracker, post_event, ids) -> None:
    presenter: str | None = None
    misses = 0
    ticks = 0
    last_keepalive = time.monotonic()

    while True:
        ticks += 1
        raw = await page.evaluate(_CAPTION_JS)
        if raw:
            misses = 0
            await tracker.update(_parse_lines(raw))
        else:
            misses += 1
            if misses in (50, 200):   # ~30s / ~2min without captions
                log.warning("no caption region — captions may be off, or "
                            "Meet's DOM changed (patch _CAPTION_JS)")

        # Cheaper checks every ~3s instead of every poll: call-ended text,
        # presenter changes, keep-alive.
        if ticks % 5 == 0:
            dead = await _page_has_dead_text(page)
            if dead:
                log.info("call over: %r", dead)
                return
            body = await page.evaluate(
                "() => document.body.innerText.slice(0, 4000)")
            m = _PRESENTING_RE.search(body or "")
            current = m.group(1).strip() if m else None
            if current and current not in (presenter, "You"):
                presenter = current
                log.info("%s started presenting", presenter)
                await post_event(presenter, "screen_share_started")
            elif not current:
                presenter = None

        if time.monotonic() - last_keepalive > KEEPALIVE_S:
            last_keepalive = time.monotonic()
            try:  # tiny mouse wiggle so Meet never flags us idle
                await page.mouse.move(200 + random.randint(0, 80),
                                      200 + random.randint(0, 80))
            except Exception:
                pass

        await asyncio.sleep(POLL_S)


async def run(meet_url: str, session_id: str, api: str,
              headless: bool, profile_dir: str | None) -> None:
    from playwright.async_api import async_playwright

    ids: dict[str, str] = {}

    async with httpx.AsyncClient(base_url=api, timeout=10) as http:

        def pid(name: str) -> str:
            return ids.setdefault(name, f"meet-{abs(hash(name)) % 10**8}")

        async def post_chunk(speaker: str, text: str) -> None:
            if speaker == "You":   # the bot itself
                return
            log.info("caption | %s: %s", speaker, text[:80])
            try:
                await http.post("/webhook/transcript", json={
                    "session_id": session_id,
                    "platform_participant_id": pid(speaker),
                    "display_name": speaker,
                    "text": text,
                    "started_at_ms": 0,
                    "duration_ms": len(text.split()) * WPM_MS,
                })
            except Exception as e:
                log.warning("webhook post failed (%s) — continuing", e)

        async def post_event(name: str, event: str) -> None:
            try:
                await http.post("/webhook/events", json={
                    "session_id": session_id,
                    "platform_participant_id": pid(name),
                    "display_name": name, "event": event, "payload": {},
                })
            except Exception as e:
                log.warning("webhook post failed (%s) — continuing", e)

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with async_playwright() as pw:
                    launch_args = [
                        "--use-fake-ui-for-media-stream",
                        "--use-fake-device-for-media-stream",
                        "--disable-blink-features=AutomationControlled",
                        "--window-size=1100,750",
                        "--lang=en-US",
                    ]
                    if profile_dir:
                        ctx = await pw.chromium.launch_persistent_context(
                            profile_dir, headless=headless, args=launch_args)
                    else:
                        browser = await pw.chromium.launch(
                            headless=headless, args=launch_args)
                        ctx = await browser.new_context(
                            permissions=[], locale="en-US",
                            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                                        "Chrome/131.0.0.0 Safari/537.36"),
                        )
                    page = ctx.pages[0] if ctx.pages else await ctx.new_page()

                    # Cut CPU/bandwidth: the bot never needs images or fonts.
                    await page.route(
                        re.compile(r"\.(png|jpe?g|gif|webp|woff2?)(\?|$)"),
                        lambda route: asyncio.ensure_future(route.abort()),
                    )

                    log.info("attempt %d/%d — opening %s",
                             attempt, MAX_ATTEMPTS, meet_url)
                    await page.goto(meet_url, wait_until="domcontentloaded")
                    await _join(page)
                    await _enable_captions(page)
                    await _scrape(page, CaptionTracker(post_chunk),
                                  post_event, ids)
                    await ctx.close()
                    return   # clean exit: call ended
            except FatalJoinError as e:
                log.error("%s", e)   # retrying cannot help — stop immediately
                return
            except Exception as e:
                log.error("attempt %d failed: %s", attempt, e)
                if attempt < MAX_ATTEMPTS:
                    log.info("rejoining in 5s… (do NOT close the Chromium "
                             "window while the bot is in the call)")
                    await asyncio.sleep(5)
        log.error("giving up after %d attempts", MAX_ATTEMPTS)


def main() -> None:
    p = argparse.ArgumentParser(description="TrueCandidate self-hosted Meet bot")
    p.add_argument("--meet-url", required=True)
    p.add_argument("--session-id", required=True)
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--headless", action="store_true",
                   help="no visible browser window (headed is more reliable)")
    p.add_argument("--profile-dir", default=None,
                   help="persistent Chrome profile dir (use a signed-in "
                        "profile for meetings that require a Google account)")
    a = p.parse_args()
    asyncio.run(run(a.meet_url, a.session_id, a.api, a.headless, a.profile_dir))


if __name__ == "__main__":
    main()
