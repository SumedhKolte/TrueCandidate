"""One-off script: rasterize the TrueCandidate mark SVG to the PNG sizes a
Chrome extension manifest requires (SVG isn't an accepted icon format there).
Reuses the Playwright/Chromium already installed for the Meet bot -- no new
dependency. Not part of the app; run manually if the logo changes:
    python -m bot._render_icons
"""
import asyncio
from pathlib import Path

SVG_PATH = Path(__file__).resolve().parents[2] / "frontend" / "public" / "logo-mark.svg"
OUT_DIR = Path(__file__).resolve().parents[2] / "extension" / "icons"
SIZES = [16, 32, 48, 128]


async def main() -> None:
    from playwright.async_api import async_playwright

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    svg = SVG_PATH.read_text(encoding="utf-8")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for size in SIZES:
            # Render at 4x then let the screenshot's clip crop to an element
            # sized exactly `size`px in CSS pixels -- device_scale_factor
            # supersamples for antialiasing while the PNG comes out at the
            # declared size (manifest icons should match their pixel dims).
            page = await browser.new_page(
                viewport={"width": size, "height": size}, device_scale_factor=1,
            )
            html = (
                f"<html><body style='margin:0;width:{size}px;height:{size}px'>"
                f"{svg}</body></html>"
            )
            await page.set_content(html)
            out = OUT_DIR / f"{size}.png"
            await page.screenshot(path=str(out), omit_background=True)
            await page.close()
            print(f"wrote {out}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
