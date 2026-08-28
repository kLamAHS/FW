"""Drive the Groups view in a real browser: who is in the North, and under whose banner.

The two questions the slice exists for, asserted through the actual UI.
"""

from __future__ import annotations

import sys
from pathlib import Path

from browsers import find_chromium  # noqa: E402  (same directory)
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8230"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/fw-groups")
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

    # 1. The Groups view lists more than noble houses.
    page.click("nav.nav button:has-text('Groups')")
    page.wait_for_timeout(1600)
    body = page.text_content(".content") or ""
    for expected in ("House Marr", "The Ironmongers of Red Ford",
                     "The Order of the Ford", "The Hillfolk", "The Grey Spears"):
        if expected not in body:
            problems.append(f"the Groups view is missing {expected!r}")
    if not problems:
        print("  houses, a guild, an order, a tribe and a free company all listed")
    page.screenshot(path=str(OUT / "groups.png"))

    # 2. Opening a house shows who belongs to it, minor houses included.
    page.click(".entity-line:has-text('House Veyne')")
    page.wait_for_timeout(1400)
    roster = page.text_content(".content") or ""
    if "House Marr" not in roster or "House Dray" not in roster:
        problems.append("House Veyne's roster does not reach its lesser houses")
    else:
        print("  House Veyne lists its vassals and their branches beneath them")
    page.screenshot(path=str(OUT / "roster.png"))

    # 3. A region answers "everything and everyone in here".
    page.click(".panel:has-text('What is inside a place') button:has-text('The Northmarch')")
    page.wait_for_timeout(1600)
    inside = page.text_content(".panel:has-text('What is inside a place')") or ""
    for expected in ("Greyhaven", "Northwatch", "The Hillfolk", "House Marr"):
        if expected not in inside:
            problems.append(f"The Northmarch's contents omit {expected!r}")
    if "Everyone in here" not in inside:
        problems.append("the region shows no roster of who is in it")
    else:
        print("  The Northmarch lists its settlements and everyone seated in them")
    page.screenshot(path=str(OUT / "inside-a-place.png"))

    # 4. A settlement's panel says where it sits.
    page.click(".panel:has-text('What is inside a place') .entity-line:has-text('Greyhaven')")
    page.wait_for_timeout(1200)
    panel = page.text_content(".side") or ""
    if "The Northmarch" not in panel or "The Kingdom of Renn" not in panel:
        problems.append("Greyhaven's panel does not show the chain it sits in")
    else:
        print("  Greyhaven's panel reads 'in The Northmarch → The Kingdom of Renn'")
    page.screenshot(path=str(OUT / "breadcrumb.png"))

    browser.close()

print(f"\nScreenshots in {OUT}")
if problems:
    print("\nPROBLEMS:")
    for line in problems:
        print("  -", line)
    sys.exit(1)
print("Groups and places both answer who and what belongs to them.")
