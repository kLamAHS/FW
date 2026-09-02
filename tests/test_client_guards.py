"""Rules the client has to keep that TypeScript cannot check for it.

There is no ESLint here and adding one is a toolchain, not a test. What this file
does instead is what `test_mapgen_guards.py` does for the Python: read the source and
assert the handful of things that have actually gone wrong.
"""

from __future__ import annotations

import pathlib
import re

CLIENT = pathlib.Path(__file__).resolve().parent.parent / "web" / "src"

# A top-level `function name(` — the boundary between one component and the next.
# Any name, not just capitalised ones: a lowercase `function useForceLayout(` is a
# boundary too, and treating it as part of the component above it made the first
# version of this guard report that component's hooks as coming after its return.
_BLOCK = re.compile(r"^(?:export\s+)?function\s+(\w+)\s*[(<]")
# A hook call in a component's own body, which is two spaces in.
_HOOK = re.compile(r"^  (?:const|let|var)?\s*.*?\buse[A-Z]\w*\s*\(")
_RETURN = re.compile(r"^  (?:if \(.*?\)\s*)?return\b")


def _blocks(lines: list[str]):
    starts = [n for n, line in enumerate(lines) if _BLOCK.match(line)]
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(lines)
        yield _BLOCK.match(lines[start]).group(1), start, end


def test_no_hook_is_called_after_an_early_return():
    """React's first rule, and the one failure mode that takes the whole app down.

    A component that returns before reaching a hook calls N hooks on one render and
    N+1 on the next, and React does not degrade: error #310 unmounts the entire root,
    so the topbar, the timeline and every other view go with it and the writer gets a
    blank page needing a reload.

    This shipped. Phase E's focus mode put `const kin = useMemo(...)` below
    `if (loading && !data) return <Loading/>`, and opening the map view emptied the
    application. Nothing caught it: `tsc` is happy — the code is well typed — the
    Python suite never renders a component, and the bug needs a real second render to
    appear, so it survives any check that only looks at the first.

    The rule read here is approximate on purpose. It reads only the component's own
    body, two spaces in, so a hook inside a nested callback is not its business, and
    it takes the FIRST top-level return as the cut. Measured against the whole client
    it reports nothing today, and it reports the shipped bug.
    """
    offences: list[str] = []
    for path in sorted(CLIENT.rglob("*.tsx")):
        lines = path.read_text().splitlines()
        for name, start, end in _blocks(lines):
            if not (name[0].isupper() or name.startswith("use")):
                continue
            hooks = [n for n in range(start, end) if _HOOK.match(lines[n])]
            returns = [n for n in range(start, end) if _RETURN.match(lines[n])]
            if not hooks or not returns:
                continue
            cut = min(returns)
            late = [n + 1 for n in hooks if n > cut]
            if late:
                offences.append(
                    f"{path.name}:{name} returns at line {cut + 1} and then calls a "
                    f"hook at {late}")
    assert not offences, (
        "a hook after an early return unmounts the whole application: "
        + "; ".join(offences))


def test_the_shipped_bundle_is_not_behind_the_source():
    """`web/dist` is committed on purpose, so it can be committed stale.

    The .gitignore says why it is tracked: "run.bat must work without Node
    installed". That makes the bundle a *shipped artefact* rather than a build
    output, and a shipped artefact that nobody rebuilds is a second, older copy of
    the application waiting to be run.

    It happened. The committed bundle sat three commits behind through Phases D and
    E, and opening the map view in it threw `labels.map is not a function` — Phase D
    had changed `draw.labels` from a list to a dict keyed by zoom band, and the
    shipped client was still the one that called `.map()` on it. Anyone running the
    app the supported way got a broken map from a repository whose tests were green.

    Compared by commit time rather than file mtime, because a fresh clone has no
    useful mtimes.
    """
    import subprocess

    root = pathlib.Path(__file__).resolve().parent.parent

    def last_touched(path: str) -> int:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", path],
            cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        return int(out) if out else 0

    source = last_touched("web/src")
    bundle = last_touched("web/dist")
    assert bundle >= source, (
        "web/src was committed after web/dist: the shipped bundle is an older "
        "application than the source. Run `npm run build` in web/ and commit it.")
