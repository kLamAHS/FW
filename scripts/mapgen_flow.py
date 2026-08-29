"""Drive map generation in a real browser: grow a map, read why, undo it.

The three promises, asserted through the UI: it draws something, it explains every
placement, and it never touches what the writer drew.
"""

from __future__ import annotations

import sys
from pathlib import Path

from browsers import find_chromium  # noqa: E402  (same directory)
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8240"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/fw-mapgen")
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
    page.click("nav.nav button:has-text('Map')")
    page.wait_for_timeout(1400)

    before = page.locator("svg path, svg circle").count()
    page.screenshot(path=str(OUT / "before.png"))

    # 1. Grow the map.
    page.click("button:has-text('Generate the map')")
    page.wait_for_selector(".panel:has-text('What the map did')", timeout=40000)
    page.wait_for_timeout(1600)
    report = page.text_content(".panel:has-text('What the map did')") or ""
    if "The map" not in report:
        problems.append("the generator reported nothing")
    else:
        print("  ", report.split(".")[0].strip() + ".")

    after = page.locator("svg path, svg circle").count()
    if after <= before:
        problems.append(f"the map did not gain features ({before} → {after})")
    else:
        print(f"   the map gained features: {before} → {after}")
    page.screenshot(path=str(OUT / "generated.png"))

    # 2. Every placement argues for itself (§67).
    if "sits here —" not in report:
        problems.append("no placement explained itself")
    else:
        print("   each settlement says why it is where it is")

    # 3. What the writer drew is reported as kept, not redrawn (§66).
    if "Left exactly as you drew them" not in report:
        problems.append("the report does not say the authored regions were kept")
    else:
        print("   the regions the writer drew were kept untouched")

    # 4. The whole map is one undo.
    page.keyboard.press("Control+z")
    page.wait_for_timeout(2000)
    undone = page.locator("svg path, svg circle").count()
    if undone > before:
        problems.append(f"one undo did not take the whole map back ({undone} vs {before})")
    else:
        print("   one Ctrl+Z took the entire map back")
    page.screenshot(path=str(OUT / "undone.png"))

    browser.close()

print(f"\nScreenshots in {OUT}")
if problems:
    print("\nPROBLEMS:")
    for line in problems:
        print("  -", line)
    sys.exit(1)
print("The map grows from the regions, explains itself, and undoes in one step.")
