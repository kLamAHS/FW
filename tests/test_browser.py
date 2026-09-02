"""The checks the Python suite cannot make: that the application actually runs.

Two of the worst defects this project has shipped were invisible to every other test
here, and both were in the client:

  * a hook called after an early return, which unmounted the whole React root — the
    map view emptied the application down to a bare div needing a page reload;
  * a wheel handler with no `preventDefault`, so zooming the map also scrolled the
    page out from under the reader.

`tsc` was happy with both, because both are well-typed code. The Python suite never
renders a component. Neither bug can be seen without a browser, a second render and a
real event, so this file supplies all three. It is slow by the standards of the rest
of the suite and worth it.

Skipped, not failed, where there is no Chromium: the suite still has to run on a
machine that has not got one.
"""

from __future__ import annotations

import pathlib
import socket
import sys
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright is not installed")


def _chromium() -> str:
    from browsers import find_chromium

    found = find_chromium()
    if not found:
        pytest.skip("no Chromium on this machine")
    return found


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def app_url(tmp_path_factory):
    """The real application, over real HTTP, on a real port."""
    import uvicorn

    from fw.api.app import create_app
    from fw.core.seed.renn import seed_renn

    path = tmp_path_factory.mktemp("browser") / "renn.fwworld"
    world = seed_renn(str(path))
    port = _free_port()
    config = uvicorn.Config(create_app(world), host="127.0.0.1", port=port,
                            log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(600):
        if server.started:
            break
        threading.Event().wait(0.05)
    if not server.started:
        pytest.skip("the server did not come up")
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
    world.close()


@pytest.fixture(scope="module")
def browser(app_url):
    executable = _chromium()
    with playwright_api.sync_playwright() as p:
        b = p.chromium.launch(executable_path=executable,
                              args=["--no-sandbox", "--disable-dev-shm-usage"])
        yield b
        b.close()


VIEWS = ("World", "Map", "History", "Family", "Relationships", "Everything")


def _open(browser, url, view, height=720):
    page = browser.new_page(viewport={"width": 1256, "height": height})
    broken: list[str] = []
    page.on("pageerror", lambda e: broken.append(f"pageerror: {e}"))
    page.on("console",
            lambda m: broken.append(f"console error: {m.text}")
            if m.type == "error" else None)
    page.goto(url, wait_until="networkidle")
    page.get_by_role("button", name=view).first.click()
    page.wait_for_timeout(2500)
    return page, broken


class TestEveryViewSurvivesBeingOpened:
    """The map view once emptied the entire application, and the only reason anybody
    found out was opening it."""

    @pytest.mark.parametrize("view", VIEWS)
    def test_the_view_opens_and_the_application_is_still_there(
            self, browser, app_url, view):
        page, broken = _open(browser, app_url, view)
        try:
            # React error #310 leaves `<div id="root"></div>` and nothing else. Any
            # unmount of the root shows up here whatever caused it.
            body = page.evaluate("() => document.getElementById('root')?.innerHTML ?? ''")
            assert len(body) > 500, (
                f"opening {view} emptied the application: root is {len(body)} bytes")
            assert page.get_by_role("button", name="Map").first.is_visible(), (
                f"opening {view} took the navigation with it")
            assert not broken, f"{view}: {broken[:3]}"
        finally:
            page.close()


class TestTheWheelZoomsTheMapAndNothingElse:
    """Over a map, the wheel means zoom. It has to be said explicitly, because a
    wheel event is a scroll gesture until something calls `preventDefault` — and
    React's own `onWheel` cannot, since React registers that listener as passive.

    Reproduced before the fix: wheeling down over the map scrolled `main.content`
    from 0 to 1031 — the whole page to the bottom — while the map zoomed out at the
    same time. Wheeling UP does not show it, because the page is already at the top:
    the direction matters, which is why this test scrolls down.
    """

    @pytest.mark.parametrize("view,selector", [
        ("Map", "svg.map-svg"),
        ("Relationships", "svg.graph-svg"),
        ("Family", "svg.pedigree-svg"),
    ])
    def test_wheeling_over_it_does_not_move_the_page(
            self, browser, app_url, view, selector):
        page, broken = _open(browser, app_url, view)
        try:
            page.wait_for_selector(selector, timeout=20000)
            page.wait_for_timeout(1200)
            probe = """(sel) => {
                const main = document.querySelector('main.content');
                const doc = document.scrollingElement;
                return {
                    main: main ? main.scrollTop : 0,
                    room: main ? main.scrollHeight - main.clientHeight : 0,
                    doc: doc.scrollTop,
                    transform: document.querySelector(sel + ' g')
                               ?.getAttribute('transform') ?? '',
                };
            }"""
            before = page.evaluate(probe, selector)
            assert before["room"] > 50, (
                "the page cannot scroll at this size, so this test proves nothing")

            box = page.locator(selector).bounding_box()
            page.mouse.move(box["x"] + box["width"] / 2,
                            box["y"] + box["height"] / 2)
            for _ in range(5):
                page.mouse.wheel(0, 200)
                page.wait_for_timeout(150)
            page.wait_for_timeout(600)
            after = page.evaluate(probe, selector)

            assert after["main"] == before["main"], (
                f"the wheel scrolled the page under {view}: "
                f"{before['main']} -> {after['main']}")
            assert after["doc"] == before["doc"], "the wheel scrolled the document"
            assert after["transform"] != before["transform"], (
                f"the wheel did not zoom {view} — is the ref on the <svg>?")
            assert not broken, f"{view}: {broken[:3]}"
        finally:
            page.close()
