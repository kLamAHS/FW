"""Drive §105 timelines in a real browser: fork, change, verify isolation, return.

The one promise that matters here — nothing done on a branch touches the main
timeline — is asserted through the actual UI, not the API.
"""

from __future__ import annotations

import sys
from pathlib import Path

from browsers import find_chromium  # noqa: E402  (same directory)
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8210"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/fw-branches")
OUT.mkdir(parents=True, exist_ok=True)

problems: list[str] = []


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=find_chromium(),
                                args=["--no-sandbox", "--disable-dev-shm-usage"])
    page = browser.new_page(viewport={"width": 1500, "height": 1000})
    page.on("console", lambda m: problems.append(f"console {m.type}: {m.text}")
            if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(f"page error: {e}"))

    page.goto(BASE, wait_until="networkidle")
    page.wait_for_selector(".topbar h1", timeout=15000)

    # 1. Fork a timeline at the current date.
    page.click(".topbar button:has-text('Timelines')")
    page.wait_for_selector(".modal-card")
    page.fill("input[aria-label='New timeline name']", "What if Marr fell")
    page.screenshot(path=str(OUT / "fork-modal.png"))
    page.click(".modal-card button:has-text('Fork the timeline')")
    page.wait_for_selector(".branch-banner", timeout=20000)
    banner = page.text_content(".branch-banner") or ""
    if "What if Marr fell" not in banner:
        problems.append(f"banner does not name the timeline: {banner!r}")
    else:
        print("  forked; the banner names the timeline")
    page.screenshot(path=str(OUT / "on-branch.png"))

    # 2. Delete an inherited connection — branch-locally.
    page.fill(".search-box input", "Greyhaven")
    page.wait_for_timeout(800)
    page.locator(".search-results .entity-line",
                 has=page.locator(".name", has_text="Greyhaven")).first.click()
    page.wait_for_timeout(900)
    before = (page.text_content(".side") or "")
    page.locator(".side .fact-actions button[title*='never true']").first.click()
    page.wait_for_timeout(300)
    page.click(".side button:has-text('really delete')")
    page.wait_for_timeout(1200)
    after = (page.text_content(".side") or "")
    if len(after) >= len(before):
        problems.append("deleting an inherited connection changed nothing on the branch")
    else:
        print("  removed an inherited connection in the branch")
    page.screenshot(path=str(OUT / "branch-edited.png"))

    # 3. The world still computes: succession renders on the branch.
    page.click("nav.nav button:has-text('Succession')")
    page.wait_for_timeout(1400)
    if "Oren" not in (page.text_content(".content") or ""):
        problems.append("succession did not render on the branch")
    else:
        print("  succession computes on the branch")

    # 4. Return to canon: the deleted connection is whole again.
    page.click(".branch-banner button:has-text('Return to the main timeline')")
    page.wait_for_selector(".topbar h1", timeout=20000)
    page.wait_for_timeout(600)
    if page.locator(".branch-banner").count():
        problems.append("returned to canon but the banner still shows")
    page.fill(".search-box input", "Greyhaven")
    page.wait_for_timeout(800)
    page.locator(".search-results .entity-line",
                 has=page.locator(".name", has_text="Greyhaven")).first.click()
    page.wait_for_timeout(900)
    canon = (page.text_content(".side") or "")
    if len(canon) < len(before):
        problems.append("canon lost content after branch edits — isolation failed")
    else:
        print("  canon kept every connection the branch removed")
    page.screenshot(path=str(OUT / "back-on-canon.png"))

    browser.close()

print(f"\nScreenshots in {OUT}")
if problems:
    print("\nPROBLEMS:")
    for line in problems:
        print("  -", line)
    sys.exit(1)
print("Timelines fork, isolate, and return — through the real UI.")
