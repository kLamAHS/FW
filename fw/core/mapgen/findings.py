"""What the map noticed and cannot silently fix.

One type, everywhere. A generator that quietly averages a contradiction, or drops a
region it could not place, is lying to the writer about their own world — so anything
the pipeline cannot resolve comes out as one of these instead, named in the writer's
words and grouped by a code the client can sort on.
"""

from __future__ import annotations

from dataclasses import dataclass

# The vocabulary of things that can go wrong. Closed, so the client can group by it and
# a typo in a code cannot silently create a category nobody displays.
CODES = (
    "contradiction",       # the writer's own notes disagree ("cold desert")
    "unsatisfiable",       # a stated fact the map could not honour
    "orphan",              # something referred to that does not exist
    "scale",               # a number that does not fit the rest of the world
    "north",               # the map had to choose an orientation
    "name-collision",      # two things the writer named the same
    "missing-predicate",   # the vocabulary this world predates
    "inherited-branch",    # canon's map, on a what-if that cannot redraw it
    "self-check",          # the generator caught itself
    "budget",              # something had to be cut to stay drawable
    "adjacency",           # a border that could not be realised on a plane
    "unplaced",            # something the map had nowhere to put
)

SEVERITIES = ("note", "warning")


@dataclass(frozen=True)
class Finding:
    """Something the map noticed and cannot silently fix.

    `message` is a sentence written for a novelist, quoting their own words where it
    can. `subjects` are stable keys — never entity ids, which are random per world and
    would make a plan's digest depend on which file it was computed in.
    """

    code: str
    severity: str
    message: str
    subjects: tuple[str, ...] = ()
    quotes: tuple[str, ...] = ()
    feature_id: str | None = None

    def __post_init__(self) -> None:
        if self.code not in CODES:
            raise ValueError(f"unknown finding code {self.code!r}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}")

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity,
                "message": self.message, "subjects": list(self.subjects),
                "quotes": list(self.quotes), "feature_id": self.feature_id}


def note(code: str, message: str, **kw) -> Finding:
    return Finding(code=code, severity="note", message=message, **kw)


def warn(code: str, message: str, **kw) -> Finding:
    return Finding(code=code, severity="warning", message=message, **kw)


def ordered(findings: list[Finding]) -> tuple[Finding, ...]:
    """Findings in a stable order: worst first, then by code, then by what they say.

    A set of findings that came back in a different order every run would change the
    plan's digest and make a golden test meaningless.
    """
    rank = {"warning": 0, "note": 1}
    return tuple(sorted(
        findings,
        key=lambda f: (rank.get(f.severity, 9), f.code, f.message, f.subjects),
    ))
