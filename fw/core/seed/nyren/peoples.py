"""Who lives here, what they speak, and where that stopped matching the borders.

The point of this file is the mismatch. A people is not a country: the Carthi lost
politically without disappearing, so their homeland is a Nyren province and there are
more Carthi in Nyren than there are Nyri anywhere. The Nyri who conquered are by now
less Nyri than the farmers still in Nyreland — they speak a language stuffed with
Carthi words, drink Merran wine, ride Orri horses and insist on their ancestry all the
harder for it.

`culture` is the entity type; `belongs_to_culture`, `speaks` and `active_in` carry the
rest, so a region can answer "who is here" separately from "who rules here".
"""

from __future__ import annotations

# people -> (homeland region, summary, whether they still govern their own homeland)
PEOPLES = {
    "The Nyri": (
        "Nyreland",
        "Northern state-builders: literate, lawyerly, and better at administration "
        "than at anything they are remembered for. They rule Nyren, which is no "
        "longer very Nyri.", True),
    "The Merra": (
        "The Merran Coast",
        "Coastal merchants who never needed one government and have spent four "
        "hundred years proving it. Rich rather than strong, and clear about the "
        "difference.", True),
    "The Carthi": (
        "The Carth Basin",
        "The river people, and the oldest civilisation on the continent. They lost "
        "their kingdoms and kept their language, their saints and their numbers.",
        False),
    "The Vardi": (
        "The Vardi Uplands",
        "Clans of the wet uplands. Wool, iron and the timber every Merran fleet is "
        "built from, which has never made them rich and has often made them angry.",
        False),
    "The Selli": (
        "The Selli North",
        "Forest and lake people, first to meet the Nyri and least remembered for it. "
        "They survive mostly in place names and in what the north still eats.", False),
    "The Talari": (
        "The Talari South",
        "Hill farmers of wine, oil and fruit. Conquered late, taxed lightly, and "
        "quietly certain they came off best.", False),
    "The Arthi": (
        "The Eastern Mountains",
        "A word outsiders use for a dozen valley peoples who do not use it about "
        "themselves. They hold the Carth's springs, which the basin holds sacred.",
        True),
    "The Orri": (
        "The Orri Steppe",
        "Horse herders beyond the rain shadow, organised into confederations that "
        "form and dissolve. Everyone's cavalry has been theirs at some point.", True),
}

# language -> (who speaks it, summary)
TONGUES = {
    "Old Nyric": ("The Nyri", "The conquest's language, now read more than spoken."),
    "Nyrenish": ("The Nyri",
                 "What Old Nyric became after four centuries in a Carthi mouth. A "
                 "scholar from Nyreland can follow perhaps half of it."),
    "Carthic": ("The Carthi",
                "Spoken from the mountains to the estuary, and by more people than any "
                "other tongue on the continent, including in Nyren's own courts."),
    "Merric": ("The Merra",
               "The language of contracts. Every port on three seas has some."),
    "Vardic": ("The Vardi", "Older than either, and confined to the valleys."),
    "Orric": ("The Orri", "Carried by horse traders as far as Hadrin's stables."),
}

# Where a people is found now, as opposed to where it started. This is the layer the
# political map cannot show, and the reason it should not be trusted alone.
LIVES_IN = {
    "The Carthi": ("The Carth Basin", "The Talari South", "The Merran Coast",
                   "The Eastern Mountains"),
    "The Nyri": ("Nyreland", "The Selli North", "The Carth Basin"),
    "The Merra": ("The Merran Coast", "The Talari South"),
    "The Selli": ("The Selli North",),
    "The Vardi": ("The Vardi Uplands", "The Merran Coast"),
    "The Talari": ("The Talari South",),
    "The Arthi": ("The Eastern Mountains",),
    "The Orri": ("The Orri Steppe", "The Carth Basin"),
}


def build(w, made: dict) -> dict:
    out: dict[str, object] = {}
    for name, (homeland, summary, _sovereign) in PEOPLES.items():
        people = w.add_entity("culture", name, summary=summary)
        out[name] = people
        w.assert_fact(people, "located_in", made[homeland],
                      note="Their homeland, which is not the same claim as governing it.")
    for name, (speakers, summary) in TONGUES.items():
        tongue = w.add_entity("language", name, summary=summary)
        out[name] = tongue
        w.assert_fact(out[speakers], "speaks", tongue)
    for people, regions in LIVES_IN.items():
        for region in regions:
            w.assert_fact(out[people], "active_in", made[region])

    # Nyrenish is Old Nyric's child and Carthic's debtor, which is the joke the whole
    # setting turns on: the conquerors' descendants speak the conquered tongue.
    w.assert_fact(out["Nyrenish"], "inherited_from", out["Old Nyric"])
    w.assert_fact(out["Nyrenish"], "inherited_from", out["Carthic"],
                  note="More of its vocabulary than any Nyren herald will admit.")
    w.assert_fact(out["The Nyri"], "speaks", out["Carthic"],
                  note="In Nyren, from the second generation onward.")
    w.assert_fact(out["The Merra"], "speaks", out["Carthic"],
                  note="Every factor in Orra, because that is who they buy from.")
    return out
