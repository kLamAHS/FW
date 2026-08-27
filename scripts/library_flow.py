"""Drive the launcher in a real browser: no world open → create → switch → example.

The complement to screenshot.py, which drives a world that is already open. This is the
double-click experience: the server starts with an empty library and everything the
writer does happens through the launcher.
"""

from __future__ import annotations

import sys
from pathlib import Path

from browsers import find_chromium  # noqa: E402  (same directory)
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8200"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/fw-library")
OUT.mkdir(parents=True, exist_ok=True)

problems: list[str] = []

with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=find_chromium(),
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    page = browser.new_page(viewport={"width": 1500, "height": 1000})
    # The launcher's own probe of /api/world legitimately answers 409 ("no world
    # open"); the browser logs every non-2xx response as a console error, so that one
    # status is expected here and only unexpected errors should fail the run.
    page.on("console", lambda m: problems.append(f"console {m.type}: {m.text}")
            if m.type == "error" and "409" not in m.text else None)
    page.on("pageerror", lambda e: problems.append(f"page error: {e}"))

    # 1. No world open: the launcher, not an error page and not a template world.
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_selector("text=No worlds yet", timeout=15000)
    page.screenshot(path=str(OUT / "launcher-empty.png"))
    print("  launcher shows, with no template world in sight")

    # 2. Create a world of one's own.
    page.fill("input[aria-label='New world name']", "My Saga")
    page.click("button:has-text('Create this world')")
    page.wait_for_selector(".topbar h1", timeout=20000)
    title = (page.text_content(".topbar h1") or "").strip()
    if title != "My Saga":
        problems.append(f"created world should open, topbar says {title!r}")
    else:
        print("  created and opened 'My Saga'")
    page.screenshot(path=str(OUT / "fresh-world.png"))

    # 3. The empty world is usable: add the first entity through the normal flow.
    page.click(".topbar > button:has-text('+ New')")
    page.wait_for_selector(".modal-card")
    page.select_option(".modal-card select", label="Person")
    page.fill(".modal-card input[placeholder*='person']", "Aro the First")
    page.click(".modal-card button:has-text('Add to the world')")
    page.wait_for_timeout(1200)
    if (page.text_content(".side h2") or "").strip() != "Aro the First":
        problems.append("first entity in an empty world did not open its panel")
    else:
        print("  the empty world accepts its first person")

    # 4. Switch worlds: create the example kingdom from the Worlds modal.
    page.click(".topbar button:has-text('Worlds')")
    page.wait_for_selector(".modal-card .entity-line")     # the listing is async
    body = page.text_content(".modal-card") or ""
    if "My Saga" not in body:
        problems.append("the Worlds modal does not list the current save")
    page.screenshot(path=str(OUT / "worlds-modal.png"))
    page.fill(".modal-card input[aria-label='New world name']", "The Tour")
    page.check(".modal-card input[type=checkbox]")
    page.click(".modal-card button:has-text('Create this world')")
    page.wait_for_selector(".topbar h1", timeout=25000)
    page.wait_for_timeout(800)
    title = (page.text_content(".topbar h1") or "").strip()
    if title != "The Kingdom of Renn":
        problems.append(f"example world should open seeded, topbar says {title!r}")
    else:
        print("  the example kingdom was created on request — and only on request")

    # 5. The seeded world actually works: succession renders the spec's answer.
    page.click("nav.nav button:has-text('Succession')")
    page.wait_for_timeout(1400)
    content = page.text_content(".content") or ""
    if "Oren" not in content:
        problems.append("succession view empty in the example world")
    else:
        print("  succession in the example world names Prince Oren")

    # 6. And switching back finds My Saga intact, first person included.
    page.click(".topbar button:has-text('Worlds')")
    page.wait_for_selector(".modal-card")
    page.click(".modal-card .entity-line:has-text('My Saga')")
    page.wait_for_selector(".topbar h1", timeout=20000)
    page.wait_for_timeout(600)
    if (page.text_content(".topbar h1") or "").strip() != "My Saga":
        problems.append("switching back to My Saga did not open it")
    else:
        page.fill(".search-box input", "Aro")
        page.wait_for_timeout(800)
        if "Aro the First" not in (page.text_content(".search-results") or ""):
            problems.append("My Saga lost its first person across the switch")
        else:
            print("  switched back; the save kept everything")
    page.screenshot(path=str(OUT / "switched-back.png"))

    browser.close()

print(f"\nScreenshots in {OUT}")
if problems:
    print("\nPROBLEMS:")
    for line in problems:
        print("  -", line)
    sys.exit(1)
print("The launcher flow works end to end.")
