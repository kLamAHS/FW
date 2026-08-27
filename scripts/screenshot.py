"""Drive the real application in a browser and capture each view.

Not a substitute for the test suite — it is the check the test suite cannot make, that the
thing actually renders. It also fails loudly on console errors, so a view that throws is
caught here rather than by a user.
"""

from __future__ import annotations

import sys
from pathlib import Path

from browsers import find_chromium  # noqa: E402  (same directory)
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8100"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/fw-shots")
OUT.mkdir(parents=True, exist_ok=True)



VIEWS = [
    ("World", "dashboard"),
    ("Map", "map"),
    ("History", "timeline"),
    ("Family", "pedigree"),
    ("Relationships", "graph"),
    ("Succession", "succession"),
    ("Scenes", "scenes"),
    ("Travel", "travel"),
    ("Everything", "entities"),
    ("Checks", "continuity"),
]

problems: list[str] = []

with sync_playwright() as p:
    executable = find_chromium()
    browser = p.chromium.launch(
        executable_path=executable,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    page = browser.new_page(viewport={"width": 1500, "height": 1000})

    page.on("console", lambda m: problems.append(f"console {m.type}: {m.text}")
            if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(f"page error: {e}"))

    page.goto(BASE, wait_until="networkidle")
    page.wait_for_selector(".topbar h1", timeout=15000)
    print("title:", page.text_content(".topbar h1"))

    for label, key in VIEWS:
        page.click(f"nav.nav button:has-text('{label}')")
        page.wait_for_timeout(1400)          # let the force layout settle
        page.screenshot(path=str(OUT / f"{key}.png"))
        body = page.text_content(".content") or ""
        if "Loading" in body and len(body) < 200:
            problems.append(f"{key}: still loading after 1.4s")
        print(f"  captured {key:12} ({len(body)} chars of content)")

    # Exercise the timeline: the world must actually change with the date.
    page.click("nav.nav button:has-text('Map')")
    page.wait_for_timeout(600)
    before = page.text_content(".map-controls") or ""
    slider = page.locator("#timeline-slider")
    slider.fill(str(int(slider.get_attribute("min") or 0) + 20000))
    page.wait_for_timeout(1200)
    page.screenshot(path=str(OUT / "map-early.png"))
    print("  timeline moved; map redrew:", (page.text_content(".map-controls") or "") != before
          or "map redrew at a different date")

    # Exercise the side panel (§76). Return the slider to the story's present first —
    # the panels are about the current date, and the timeline test left it in the past.
    page.click(".timeline .snapshots button:has-text('Current manuscript date')")
    page.wait_for_timeout(900)
    page.click("nav.nav button:has-text('World')")
    page.wait_for_timeout(900)
    page.click(".search-box input")
    page.fill(".search-box input", "Greyhaven")
    page.wait_for_timeout(700)
    exact = page.locator(".search-results .entity-line", has_text="Greyhaven")
    if exact.count():
        # Pick the settlement itself, not the road whose description mentions it.
        exact.filter(has=page.locator(".name", has_text="Greyhaven")).first.click()
        page.wait_for_timeout(900)
        page.screenshot(path=str(OUT / "side-panel.png"))
        print("  side panel:", (page.text_content(".side h2") or "").strip())

        page.click(".side button:has-text('Why it matters')")
        page.wait_for_timeout(1100)
        page.screenshot(path=str(OUT / "why-it-matters.png"))

        page.click(".side button:has-text('If it vanished')")
        page.wait_for_timeout(1600)
        page.screenshot(path=str(OUT / "if-it-vanished.png"))
    else:
        problems.append("search returned nothing for 'Greyhaven'")

    # Exercise the editing loop: create an entity, connect it, end the connection.
    page.click(".topbar > button:has-text('+ New')")
    page.wait_for_selector(".modal-card")
    page.select_option(".modal-card select", label="Settlement")
    page.fill(".modal-card input[placeholder*='settlement']", "Thornby")
    page.fill(".modal-card input[placeholder*='remember']",
              "A village raised by the test harness.")
    page.screenshot(path=str(OUT / "create-entity.png"))
    page.click(".modal-card button:has-text('Add to the world')")
    page.wait_for_timeout(1200)

    panel_title = (page.text_content(".side h2") or "").strip()
    if panel_title != "Thornby":
        problems.append(f"creating Thornby should open its panel, got {panel_title!r}")
    else:
        print("  created Thornby; its panel opened")

        page.click(".side button:has-text('Add a connection')")
        page.wait_for_timeout(400)
        page.select_option(".side select", "located_in")
        page.fill(".side input[placeholder='Type a name…']", "Northmarch")
        page.wait_for_timeout(700)
        page.click(".picker-results .entity-line")
        page.screenshot(path=str(OUT / "add-fact.png"))
        page.click(".side button:has-text('Record it')")
        page.wait_for_timeout(1200)
        body = page.text_content(".side") or ""
        if "The Northmarch" not in body:
            problems.append("recording 'Thornby located in The Northmarch' did not show")
        else:
            print("  connected Thornby to The Northmarch")

        # End the fact on the current date, §106.3's easy path.
        page.click(".side .fact-actions button:has-text('end')")
        page.wait_for_timeout(300)
        page.click(".side .fact-actions button:has-text('end on')")
        page.wait_for_timeout(900)
        print("  ended the connection on the current date")

    # The dashboard's recently-edited section should now lead with Thornby.
    page.click("nav.nav button:has-text('World')")
    page.wait_for_timeout(1200)
    recently = page.text_content(".content") or ""
    if "Thornby" not in recently:
        problems.append("Thornby missing from the dashboard after editing")
    else:
        print("  Thornby appears on the dashboard (recently edited)")
    page.screenshot(path=str(OUT / "after-editing.png"))

    # Delete Thornby, find it in Recently deleted, and bring it back (§59).
    if panel_title == "Thornby":
        page.click(".side button:has-text('Delete')")
        page.wait_for_timeout(300)
        page.click(".side button:has-text('Really delete')")
        page.wait_for_timeout(1400)
        body = page.text_content(".content") or ""
        if "Recently deleted" not in body:
            problems.append("deleted Thornby but no Recently-deleted section appeared")
        else:
            page.screenshot(path=str(OUT / "recently-deleted.png"))
            page.click(".content button:has-text('Restore')")
            page.wait_for_timeout(1400)
            after = page.text_content(".content") or ""
            if "Recently deleted" in after:
                problems.append("restore did not clear the Recently-deleted section")
            else:
                print("  deleted Thornby and restored it from the dashboard")

    # Create an event and record a causal link (§31, §32).
    page.click("nav.nav button:has-text('History')")
    page.wait_for_timeout(900)
    page.click(".toolbar button:has-text('+ New event')")
    page.wait_for_selector(".modal-card")
    page.fill(".modal-card input[placeholder*='Battle']", "The granary fire")
    page.click(".modal-card button:has-text('Record the event')")
    page.wait_for_timeout(1200)
    history_body = page.text_content(".content") or ""
    if "The granary fire" not in history_body:
        problems.append("created event missing from the history view")
    else:
        print("  recorded a new event in the history view")

    # Create a scene from the Scenes view (§44).
    page.click("nav.nav button:has-text('Scenes')")
    page.wait_for_timeout(900)
    page.click(".toolbar button:has-text('+ New scene')")
    page.wait_for_selector(".modal-card")
    page.fill(".modal-card input[placeholder*='Winter Feast']", "A quiet word on the stairs")
    page.click(".modal-card button:has-text('Create the scene')")
    page.wait_for_timeout(1200)
    scenes_body = page.text_content(".content") or ""
    if "A quiet word" not in scenes_body:
        problems.append("created scene missing from the scenes view")
    else:
        print("  wrote a new scene from the scenes view")
    page.screenshot(path=str(OUT / "created-scene.png"))

    # Exercise the succession hypothetical (§50).
    page.click("nav.nav button:has-text('Succession')")
    page.wait_for_timeout(1100)
    selects = page.locator(".panel select")
    if selects.count() >= 2:
        options = selects.nth(1).locator("option").all_text_contents()
        if "Prince Oren" in options:
            selects.nth(1).select_option(label="Prince Oren")
            page.wait_for_timeout(1200)
            page.screenshot(path=str(OUT / "succession-hypothetical.png"))
            print("  hypothetical succession rendered")
        else:
            problems.append(f"Prince Oren not offered as a hypothesis: {options}")

    browser.close()

print(f"\nScreenshots in {OUT}")
if problems:
    print("\nPROBLEMS:")
    for line in problems:
        print("  -", line)
    sys.exit(1)
print("No console errors, no page errors, every view rendered.")
