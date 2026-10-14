"""Download every receipt photo from the task portal into data/images/ with Playwright.

The portal URL is read from PORTAL_URL in .env and is never printed. The photo list is
virtualised (only ~130 rows rendered at a time), so the script scrolls its container
step by step, downloading each newly rendered row it does not already have on disk.

Usage: python scripts/fetch_images.py [--images-dir DIR] [--headless]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from receipts.normalize import image_key  # noqa: E402

EXPECTED_COUNT = 467
CLICK_PAUSE_S = 0.3
RETRIES = 2                  # extra attempts after the first failed click
DOWNLOAD_TIMEOUT_MS = 15_000
GIVE_UP_AFTER = 5            # consecutive failures before any success: the page is not what we expect
STALL_SCROLLS = 3            # scrolls in a row that reveal nothing new before stopping

# "/" is escaped because Playwright hands the pattern to the page as a JS regex literal.
ROW_TEXT = re.compile(r"^\s*images\/", re.I)
ROW_NAME = re.compile(r"images/(\S+?\.(?:jpe?g|png|heic|webp))", re.I)

# Nearest ancestor that scrolls vertically; falls back to the page itself.
FIND_SCROLLER = """el => {
  for (let n = el.parentElement; n; n = n.parentElement) {
    const oy = getComputedStyle(n).overflowY;
    if ((oy === 'auto' || oy === 'scroll' || oy === 'overlay') && n.scrollHeight > n.clientHeight + 1) return n;
  }
  return document.scrollingElement;
}"""


class Redactor:
    """Strips the portal URL (and its query string) out of any text before it is printed."""

    def __init__(self, url: str) -> None:
        self.secrets = sorted({url, url.split("?", 1)[0], *url.split("?", 1)[1:]} - {""}, key=len, reverse=True)

    def __call__(self, text: object) -> str:
        s = str(text)
        for secret in self.secrets:
            s = s.replace(secret, "<PORTAL_URL>")
        return s


def rendered_names(page: Page) -> list[str]:
    texts = page.locator("button").filter(has_text=ROW_TEXT).evaluate_all("els => els.map(e => e.innerText)")
    return [m.group(1).split("/")[-1] for t in texts if (m := ROW_NAME.search(t))]


def wait_for_rows(page: Page, timeout_s: float) -> bool:
    """True once photo rows are rendered, expanding the "N photos" section if it is collapsed."""
    deadline = time.monotonic() + timeout_s
    expanded = False
    while time.monotonic() < deadline:
        if rendered_names(page):
            return True
        toggle = page.get_by_text(re.compile(r"\b\d+\s+photos\b", re.I)).first
        if not expanded and toggle.count() and toggle.is_visible():
            toggle.click()
            expanded = True
        page.wait_for_timeout(500)
    return False


def download_one(page: Page, name: str, dest: Path, redact: Redactor) -> str | None:
    """Click the row for `name` and save the download. Returns an error message, or None."""
    error = None
    for attempt in range(1 + RETRIES):
        if attempt:
            page.wait_for_timeout(1000)
        try:
            row = page.locator("button").filter(has_text=re.compile(rf"images\/{re.escape(name)}\b")).first
            with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as info:
                row.click(timeout=DOWNLOAD_TIMEOUT_MS)
            download = info.value
            if download.failure():
                raise RuntimeError(f"download failed: {download.failure()}")
            tmp = dest.with_suffix(dest.suffix + ".part")
            download.save_as(tmp)
            if tmp.stat().st_size == 0:
                tmp.unlink()
                raise RuntimeError("empty file")
            tmp.replace(dest)
            return None
        except (PlaywrightError, RuntimeError, OSError) as exc:
            error = redact(exc).strip().splitlines()[0]
    return error


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--images-dir", type=Path, default=REPO / "data" / "images")
    ap.add_argument("--headless", action="store_true", help="no browser window (no chance to log in)")
    args = ap.parse_args()

    load_dotenv(REPO / ".env")
    url = os.environ.get("PORTAL_URL", "").strip()
    if not url:
        print("PORTAL_URL is not set in .env", file=sys.stderr)
        return 2
    redact = Redactor(url)
    images_dir = args.images_dir.resolve()
    images_dir.mkdir(parents=True, exist_ok=True)
    have = {image_key(p.name) for p in images_dir.iterdir() if p.is_file()}

    seen: list[str] = []
    saved, skipped, failed = [], [], {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=args.headless)
            page = browser.new_context(accept_downloads=True).new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)

            if not wait_for_rows(page, 30):
                if args.headless:
                    print("No photo list after 30 s and running headless; cannot log in.", file=sys.stderr)
                    return 3
                print("Log in in the opened browser, then press Enter")
                try:
                    input()
                except EOFError:
                    print("No terminal to read Enter from; run this script yourself to log in.", file=sys.stderr)
                    return 3
                if not wait_for_rows(page, 60):
                    print("Still no photo rows (buttons named images/...) on the page.", file=sys.stderr)
                    return 3

            stalls = 0
            while stalls < STALL_SCROLLS:
                new = [n for n in dict.fromkeys(rendered_names(page)) if n not in set(seen)]
                seen += new
                for name in new:
                    if image_key(name) in have:
                        skipped.append(name)
                        continue
                    error = download_one(page, name, images_dir / name, redact)
                    if error:
                        failed[name] = error
                        print(f"  FAILED {name}: {error}")
                        if not saved and len(failed) >= GIVE_UP_AFTER:
                            print(f"{GIVE_UP_AFTER} failures and no success: stopping. "
                                  "The rows may not trigger a download when clicked.", file=sys.stderr)
                            return 4
                    else:
                        saved.append(name)
                        have.add(image_key(name))
                        if len(saved) % 25 == 0:
                            print(f"  saved {len(saved)} (rows seen {len(seen)})")
                    page.wait_for_timeout(int(CLICK_PAUSE_S * 1000))

                rows = page.locator("button").filter(has_text=ROW_TEXT)
                if not rows.count():
                    break
                scroller = rows.first.evaluate_handle(FIND_SCROLLER)
                before = scroller.evaluate("s => s.scrollTop")
                scroller.evaluate("s => { s.scrollTop += Math.max(200, s.clientHeight * 0.8); }")
                page.wait_for_timeout(400)
                moved = scroller.evaluate("s => s.scrollTop") != before
                stalls = 0 if new or moved else stalls + 1
                if not moved and not new:
                    page.wait_for_timeout(600)  # give a lazy list one more moment to render
            browser.close()
    except PlaywrightError as exc:
        print(f"Browser error: {redact(exc).strip().splitlines()[0]}", file=sys.stderr)
        return 5
    finally:
        on_disk = sum(1 for p in images_dir.iterdir() if p.is_file() and not p.name.endswith(".part"))
        print(f"\nRows seen: {len(seen)} (expected {EXPECTED_COUNT})")
        print(f"Saved: {len(saved)}")
        print(f"Skipped, already present: {len(skipped)}")
        print(f"Failed: {len(failed)}")
        for name, error in failed.items():
            print(f"    {name}: {error}")
        print(f"Files now in {images_dir.name}/: {on_disk}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
