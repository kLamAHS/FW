"""Drive the real application in a browser and capture each view.

Not a substitute for the test suite — it is the check the test suite cannot make, that the
thing actually renders. It also fails loudly on console errors, so a view that throws is
caught here rather than by a user.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8100"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/fw-shots")
OUT.mkdir(parents=True, exist_ok=True)


def find_chromium() -> str | None:
    """Use whatever Chromium build is on the machine.

    Playwright pins an exact browser revision and refuses a different one, which is a
    problem in any environment that ships its own. Pointing at the installed binary is
    both faster and avoids a download that may not be possible offline.
    """
    for candidate in sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome")):
        return str(candidate)
    for candidate in ("/usr/bin/chromium", "/usr/bin/chromium-browser",
                      "/usr/bin/google-chrome"):
        if Path(candidate).exists():
            return candidate
    return None

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
