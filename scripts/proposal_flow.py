"""Drive the propose-then-accept flow in a real browser (§66).

The point of the whole slice is that the writer sees a map before it exists and can
turn parts of it down. That is a claim about what appears on screen, so it is checked
on screen.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import expect, sync_playwright  # noqa: E402

from scripts.browsers import find_chromium  # noqa: E402


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8300"
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/proposal")
    out.mkdir(parents=True, exist_ok=True)
    binary = find_chromium()
    if binary is None:
        print("no chromium found")
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=binary, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        page.goto(base, wait_until="networkidle")

        page.get_by_role("button", name="Map").first.click()
        page.wait_for_selector("svg.map-svg")
        before = page.locator("svg.map-svg polygon, svg.map-svg polyline").count()

        page.get_by_role("button", name="Propose a map").click()
        page.wait_for_selector(".proposal", timeout=30_000)
        summary = page.locator(".proposal-head strong").inner_text()
        print("proposal:", summary)
        expect(page.locator(".proposal-overlay")).to_be_visible()
        page.screenshot(path=str(out / "01-proposed.png"))

        # open a group and turn one feature down
        page.locator(".proposal-group h4 button.link").first.click()
        page.wait_for_selector(".proposal-list li")
        first = page.locator(".proposal-list li").first
        name = first.locator("input.proposal-name, strong").first
        print("first feature:", name.input_value()
              if name.evaluate("e => e.tagName") == "INPUT" else name.inner_text())
        print("reason:", first.locator("p.muted").inner_text())
        checkbox = first.locator("input[type=checkbox]")
        checkbox.uncheck()
        page.screenshot(path=str(out / "02-one-refused.png"))

        keep = page.locator(".proposal-actions button.primary")
        label = keep.inner_text()
        print("button says:", label)
        assert " of " in label

        # nothing written yet
        assert page.locator("svg.map-svg polygon, svg.map-svg polyline").count() >= before

        keep.click()
        page.wait_for_selector("text=What the map did", timeout=30_000)
        print("after:", page.locator(".panel:has-text('What the map did') p.small")
              .first.inner_text())
        page.screenshot(path=str(out / "03-applied.png"))
        browser.close()
    print("proposal flow ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
