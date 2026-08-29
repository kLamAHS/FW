"""Drive the era editor in a real browser: declare a backward age, see dates change.

The point of §3's dividers is that a world can name its own BC — this asserts it through
the actual UI, including that a date can be *typed* in era terms, not only read.
"""

from __future__ import annotations

import sys
from pathlib import Path

from browsers import find_chromium  # noqa: E402  (same directory)
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8220"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/fw-eras")
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

    # 1. The seeded world already names its ages, and the present date reads in one.
    present = page.text_content(".timeline .date") or ""
    if "AK" not in present:
        problems.append(f"the current date does not name its age: {present!r}")
    else:
        print(f"  the present reads as {present.strip()!r}")

    # 2. The ages editor lists them, backward age included.
    page.click(".timeline button:has-text('ages')")
    page.wait_for_selector(".modal-card")
    listed = page.text_content(".modal-card") or ""
    for expected in ("Age of Kings", "The Long Dark", "counting backwards"):
        if expected not in listed:
            problems.append(f"the ages editor does not show {expected!r}")
    page.screenshot(path=str(OUT / "ages-editor.png"))

    # 3. Declare a new age and see it listed.
    page.fill(".modal-card input[aria-label='Era name']", "Age of Ash")
    page.fill(".modal-card input[aria-label='Era short form']", "AA")
    page.fill(".modal-card input[aria-label='First year']", "400")
    page.click(".modal-card button:has-text('Add this age')")
    page.wait_for_timeout(1200)
    if "Age of Ash" not in (page.text_content(".modal-card") or ""):
        problems.append("a newly declared age did not appear in the list")
    else:
        print("  declared a new age from the editor")
    page.screenshot(path=str(OUT / "age-added.png"))
    page.click(".modal-card button[aria-label='Close']")
    page.wait_for_timeout(600)

    # 4. A date can be TYPED in a backward age — the half that makes BC usable.
    page.click(".topbar > button:has-text('+ New')")
    page.wait_for_selector(".modal-card")
    page.select_option(".modal-card select", label="Person")
    page.fill(".modal-card input[placeholder*='person']", "Elder of the Dark")
    page.click(".modal-card button:has-text('More detail')")
    page.wait_for_timeout(400)
    page.fill(".modal-card input[aria-label='Born: year']", "120")
    page.select_option(".modal-card select[aria-label='Born: age']", "BD")
    page.screenshot(path=str(OUT / "typed-in-era.png"))
    page.click(".modal-card button:has-text('Add to the world')")
    page.wait_for_timeout(1400)

    panel = (page.text_content(".side h2") or "").strip()
    if panel != "Elder of the Dark":
        problems.append(f"creating the elder should open their panel, got {panel!r}")
    else:
        # the stored date must read back in the same age it was typed in
        page.click(".side button:has-text('Edit')")
        page.wait_for_timeout(600)
        page.click(".side button:has-text('More detail')")   # dates live behind it
        page.wait_for_timeout(900)
        year = page.input_value(".side input[aria-label='Born: year']")
        era = page.input_value(".side select[aria-label='Born: age']")
        if (year, era) != ("120", "BD"):
            problems.append(f"a date typed as 120 BD read back as {year!r} {era!r}")
        else:
            print("  a date typed as '120 BD' stores and reads back as 120 BD")
    page.screenshot(path=str(OUT / "era-round-trip.png"))

    browser.close()

print(f"\nScreenshots in {OUT}")
if problems:
    print("\nPROBLEMS:")
    for line in problems:
        print("  -", line)
    sys.exit(1)
print("A world can name its own ages, and dates round-trip through them.")
