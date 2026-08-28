"""Shared browser discovery for the driving scripts."""

from __future__ import annotations

from pathlib import Path


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
