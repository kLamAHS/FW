"""Succession laws (spec §8).

The brief lists absolute, male-preference and male-only primogeniture, ultimogeniture,
seniority, elective monarchy, tanistry, appointment, conquest and hereditary office — and
then asks for custom rule scripting on top.

Rather than let writers supply Python (which would mean executing arbitrary code from a
project file — an unacceptable thing to do to someone who downloaded a world from the
internet), a law is **declarative configuration**: which relatives are eligible, how
siblings are ordered, whether the walk is depth-first through each claimant's own line, and
which disqualifications apply. That covers every named system and most invented ones, and a
world file can never execute anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Order(str, Enum):
    """How siblings are ranked against each other."""

    ELDEST_FIRST = "eldest_first"
    YOUNGEST_FIRST = "youngest_first"
    MALE_PREFERENCE = "male_preference"      # all brothers, then all sisters, each by age
    FEMALE_PREFERENCE = "female_preference"


@dataclass(frozen=True)
class SuccessionLaw:
    """A declarative succession rule.

    `depth_first` is the difference between primogeniture and seniority, and it is the
    single most consequential flag here. Under primogeniture a claimant's own children
    outrank that claimant's younger siblings — the crown descends through a line before it
    moves sideways. Under seniority the whole generation is exhausted first.
    """

    key: str
    label: str
    order: Order = Order.MALE_PREFERENCE
    depth_first: bool = True
    allowed_genders: tuple[str, ...] = ()        # empty means no restriction
    require_legitimate: bool = True
    # A tanistry-style law limits the pool to close kin of the last holder.
    max_degree: int | None = None
    # Some offices are not inherited at all.
    hereditary: bool = True
    description: str = ""

    def permits_gender(self, gender: str | None) -> bool:
        if not self.allowed_genders:
            return True
        return (gender or "unknown") in self.allowed_genders

    def sibling_key(self, person) -> tuple:
        """Sort key placing siblings in order of claim."""
        born = person.born if person.born is not None else 0
        match self.order:
            case Order.ELDEST_FIRST:
                return (born, person.name)
            case Order.YOUNGEST_FIRST:
                return (-born, person.name)
            case Order.MALE_PREFERENCE:
                return (0 if person.gender == "male" else 1, born, person.name)
            case Order.FEMALE_PREFERENCE:
                return (0 if person.gender == "female" else 1, born, person.name)
        return (born, person.name)  # pragma: no cover


LAWS: dict[str, SuccessionLaw] = {
    law.key: law
    for law in (
        SuccessionLaw(
            "absolute_primogeniture", "Absolute primogeniture",
            order=Order.ELDEST_FIRST, depth_first=True,
            description="The eldest child inherits regardless of gender.",
        ),
        SuccessionLaw(
            "male_preference_primogeniture", "Male-preference primogeniture",
            order=Order.MALE_PREFERENCE, depth_first=True,
            description="Sons before daughters, each by age; a daughter inherits only "
                        "when no son's line survives.",
        ),
        SuccessionLaw(
            "female_preference_primogeniture", "Female-preference primogeniture",
            order=Order.FEMALE_PREFERENCE, depth_first=True,
        ),
        SuccessionLaw(
            "male_only_primogeniture", "Male-only primogeniture",
            order=Order.ELDEST_FIRST, depth_first=True, allowed_genders=("male",),
            description="Women cannot inherit and cannot transmit a claim.",
        ),
        SuccessionLaw(
            "female_only_primogeniture", "Female-only primogeniture",
            order=Order.ELDEST_FIRST, depth_first=True, allowed_genders=("female",),
        ),
        SuccessionLaw(
            "ultimogeniture", "Ultimogeniture",
            order=Order.YOUNGEST_FIRST, depth_first=True,
            description="The youngest child inherits — common where elder children are "
                        "provided for during the holder's lifetime.",
        ),
        SuccessionLaw(
            "seniority", "Seniority",
            order=Order.ELDEST_FIRST, depth_first=False,
            description="The eldest surviving member of the dynasty inherits, so the "
                        "title moves sideways through a generation before descending.",
        ),
        SuccessionLaw(
            "agnatic_seniority", "Agnatic seniority",
            order=Order.ELDEST_FIRST, depth_first=False, allowed_genders=("male",),
        ),
        SuccessionLaw(
            "tanistry", "Tanistry",
            order=Order.ELDEST_FIRST, depth_first=False, max_degree=2,
            description="A successor chosen from among close kin of the holder.",
        ),
        SuccessionLaw(
            "elective", "Elective",
            order=Order.ELDEST_FIRST, depth_first=False, hereditary=False,
            require_legitimate=False,
            description="Chosen by an electorate. The computed order is advisory only — "
                        "the writer decides the outcome.",
        ),
        SuccessionLaw(
            "appointment", "Appointment",
            hereditary=False, require_legitimate=False,
            description="Held at the pleasure of a superior; no automatic heir.",
        ),
        SuccessionLaw(
            "conquest", "Conquest",
            hereditary=False, require_legitimate=False,
            description="Held by force. Succession is whoever can take and keep it.",
        ),
    )
}


def get_law(key: str) -> SuccessionLaw:
    """Look up a law, falling back to male-preference primogeniture.

    A world that names a law this build does not know should still open and still compute
    *something* rather than refusing to display a title.
    """
    return LAWS.get(key, LAWS["male_preference_primogeniture"])
