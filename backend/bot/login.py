"""
One-time Google sign-in for the self-hosted Meet bot.

Google blocks anonymous guests from most Meet calls ("You can't join this
video call"), so the bot needs a signed-in identity. This opens a real browser
against a PERSISTENT profile directory; you sign in by hand once, and the bot
reuses those cookies forever after.

    python -m bot.login                    # writes ./bot_profile
    python -m bot.login --profile-dir X    # custom location

Then set in backend/.env:
    MEET_BOT_PROFILE_DIR=./bot_profile

Use a throwaway/service Google account, not your personal one — the profile
directory holds live session cookies. Keep it out of git (.gitignore covers
bot_profile/).
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path


async def run(profile_dir: str) -> None:
    from playwright.async_api import async_playwright

    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    print(f"\nOpening a browser with profile: {profile_dir}")
    print("1. Sign into the Google account the bot should use.")
    print("2. Visit https://meet.google.com once so the session settles.")
    print("3. Close the browser window when done — cookies persist.\n")

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            profile_dir,
            headless=False,
            args=["--disable-blink-features=AutomationControlled",
                  "--window-size=1200,820", "--lang=en-US"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://accounts.google.com/")
        # Wait until the operator closes the window.
        closed = asyncio.Event()
        ctx.on("close", lambda _: closed.set())
        await closed.wait()

    print(f"\nProfile saved. Add this to backend/.env:\n"
          f"  MEET_BOT_PROFILE_DIR={profile_dir}\n")


def main() -> None:
    p = argparse.ArgumentParser(description="One-time Google login for the Meet bot")
    p.add_argument("--profile-dir", default="./bot_profile")
    asyncio.run(run(p.parse_args().profile_dir))


if __name__ == "__main__":
    main()
