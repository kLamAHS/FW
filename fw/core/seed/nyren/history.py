"""What happened, in the order it happened, and who tells it which way.

The conquest is deliberately not one date. It ran from 612 to 653 and people argue
about both ends: Nyren dates its crown from the submission at Hadrin in Goldfall 653,
Merran has never accepted that anything was concluded, and Carthain counts from 61 and
regards all of this as recent.

Four stages, because a continent is not taken in an afternoon. An invited army; a
protectorate; a dynastic marriage that made conquest look like inheritance; and then
forty years of war that ended at a coastline the Nyri could beat in the field and could
not besiege.
"""

from __future__ import annotations

# The three realms, the two that are gone, and the homeland across the sea.
REALMS = {
    "Nyren": ("Most of the continent, ruled from Hadrin. Nyri in its crown, Carthi in "
              "its fields, and increasingly unsure which of those it is.", 653, None),
    "Merran": ("Five cities and the coast between them. No king, a council that "
               "changes hands, and more money than Nyren has ever been able to "
               "borrow.", 210, None),
    "Carthain": ("What is left of Carth, high in the valley behind the mountains. The "
                 "oldest crown here, and the weakest.", 61, None),
    "The Kingdom of Nyreland": ("The Nyri homeland, and for two hundred years now a province of its "
                 "own colony.", 402, None),
    "The Kingdom of Lower Carth": ("The richest of the Carthi kingdoms, and the one "
                                   "whose succession war invited the Nyri in.", 96, 631),
    "The Kingdom of Upper Carth": ("Swallowed in 671. Its heirs went to Orath and have "
                                   "been calling themselves Carthain ever since.",
                                   88, 671),
}

# The chronology the brief lays out, as events a reader can walk.
CHRONICLE = [
    ("The Succession of Lower Carth", "event", (609,), None,
     "Threeforks",
     "King Belan of Lower Carth died leaving two sons and no arrangement."),
    ("The Nyri Landing at Nyrmark", "event", (612, 4, 11), None, "Nyrmark",
     "Eighteen thousand men, with engineers, clerks, surveyors and interpreters. "
     "An expeditionary army, not a raid."),
    ("The Intervention", "war", (612, 5, 20), (631,), "The Carth Basin",
     "Nyri troops fought Lower Carth's succession war, won it, and were paid in land."),
    ("The Protectorate", "event", (631,), None, "Threeforks",
     "Nyri officials took the customs and the levy. The Carthi king kept the throne "
     "and stopped deciding anything."),
    ("The Marriage at Threeforks", "event", (638, 3, 22), None, "Threeforks",
     "A Nyri prince married a Carthi princess, and their son inherited both claims. "
     "After this the Nyri stopped saying they were conquering anything."),
    ("The Continental War", "war", (640,), (653,), "The Carth Basin",
     "Thirteen years in which the rest of the continent found out what the marriage "
     "had meant."),
    ("The Submission at Hadrin", "event", (653, 6, 3), None, "Hadrin",
     "The last Carthi and Talari lords set down their crowns. Nyren dates itself from "
     "this day; Merran sent no one."),
    ("The Vardi Risings", "war", (705,), (721,), "The Vardi Uplands",
     "Sixteen years of upland revolt, put down at a cost nobody in Hadrin published."),
    ("The First Siege of Orra", "war", (727,), (729,), "Orra",
     "Thirty thousand men, eighteen months, and a fleet that never stopped coming in. "
     "Disease ended it, not the walls."),
    ("The Second Siege of Orra", "war", (801,), (802,), "Orra",
     "His grandson tried it again with more engineers and less time."),
    ("The Treaty of Sere", "treaty", (806, 7, 19), None, "Sere",
     "Nyren acknowledged the Merran coast. Neither side has ever called it permanent."),
    ("The Grain Edict", "event", (1094, 2, 30), None, "Hadrin",
     "Nyren forbade Carthi grain to leave except through Nyren factors. Orra's "
     "warehouses were full within a season and the price collapsed."),
]

# The same day, told by the people who were there. §33's whole point.
ACCOUNTS = [
    ("The Submission at Hadrin", "Nyren", "The Crown's account",
     "The day the wars between the valleys ended and one law began."),
    ("The Submission at Hadrin", "Carthain", "The Orath chronicle",
     "The day a foreign captain sat in a Carthi hall and called himself its heir."),
    ("The Submission at Hadrin", "Merran", "The Meret record",
     "A ceremony in an inland town, attended by those who had already lost."),
    ("The First Siege of Orra", "Nyren", "The Crown's account",
     "A campaign abandoned on account of fever in the camp."),
    ("The First Siege of Orra", "Merran", "The Meret record",
     "Eighteen months during which the city ate better than the army outside it."),
]


def build(w, made: dict) -> dict:
    d = w.day
    out: dict[str, object] = {}

    for name, (summary, began, ended) in REALMS.items():
        realm = w.add_entity("realm", name, summary=summary, exists_from=d(began),
                             exists_to=d(ended) if ended else None)
        out[name] = realm
    w.assert_fact(out["Nyren"], "government", value="feudal monarchy, heavily clerked")
    w.assert_fact(out["Merran"], "government", value="merchant oligarchy")
    w.assert_fact(out["Carthain"], "government", value="hereditary kingship")
    w.assert_fact(out["The Kingdom of Nyreland"], "government", value="crown province of Nyren")

    # Who holds what now, which is not who lives there. Nyren's writ runs over six of
    # the eight regions; the mountains are tributary rather than held, and the steppe
    # is nobody's.
    for region in ("The Carth Basin", "The Selli North", "The Vardi Uplands",
                   "The Talari South", "Nyreland"):
        w.assert_fact(out["Nyren"], "legally_owns", made[region], valid_from=d(653))
    w.assert_fact(out["Merran"], "legally_owns", made["The Merran Coast"], valid_from=d(210))
    w.assert_fact(out["Carthain"], "legally_owns", made["The Eastern Mountains"],
                  valid_from=d(671))
    w.assert_fact(out["Nyren"], "taxes", made["The Eastern Mountains"],
                  valid_from=d(724),
                  note="Tribute, which Orath pays and does not call tax.")
    # §11: four authorities over one place, and the whole plot in one fact.
    w.assert_fact(out["Merran"], "legally_owns", made["Orra"], valid_from=d(210))
    w.assert_fact(out["Nyren"], "claims", made["Orra"], valid_from=d(727),
                  note="On the grounds that the river above it is Nyren's, and a river "
                       "is one thing.")
    # Threeforks, not the whole basin. Carthain's claim is to the Carthi crown and to
    # the city its kings were made in, which is a pointed thing to say at a coronation;
    # claiming every acre from the mountains to the sea would be a different and much
    # stupider claim, and would paint half the continent as disputed ground.
    w.assert_fact(out["Carthain"], "claims", made["Threeforks"], valid_from=d(671),
                  note="Advanced at every coronation since, and taken seriously by "
                       "nobody who has an army.")

    w.assert_fact(out["Nyren"], "rival_of", out["Merran"])
    w.assert_fact(out["Nyren"], "at_war_with", out["Merran"], valid_from=d(727),
                  valid_to=d(806), note="On and off for eighty years.")
    w.assert_fact(out["Carthain"], "legitimacy", value="ancient and unenforceable")
    w.assert_fact(out["Nyren"], "legitimacy", value="conquest, marriage, and four "
                                                    "centuries of nobody undoing it")
    w.assert_fact(out["Merran"], "legitimacy", value="charter and possession")

    # -------------------------------------------------------------- chronicle
    def when(spec):
        return d(*spec)

    events: dict[str, object] = {}
    for name, kind, start, end, where, summary in CHRONICLE:
        events[name] = w.add_event(
            name, type_key=kind, summary=summary, start_day=when(start),
            end_day=when(end) if end else None,
            location_id=made[where].id if where in made else None)
    out |= events

    # §32: the causal chain that produced a continent with two capitals.
    chain = [
        ("The Succession of Lower Carth", "The Nyri Landing at Nyrmark", "caused"),
        ("The Nyri Landing at Nyrmark", "The Intervention", "caused"),
        ("The Intervention", "The Protectorate", "caused"),
        ("The Protectorate", "The Marriage at Threeforks", "contributed_to"),
        ("The Marriage at Threeforks", "The Continental War", "caused"),
        ("The Continental War", "The Submission at Hadrin", "caused"),
        ("The Submission at Hadrin", "The Vardi Risings", "contributed_to"),
        ("The Submission at Hadrin", "The First Siege of Orra", "contributed_to"),
        ("The First Siege of Orra", "The Second Siege of Orra", "contributed_to"),
        ("The Second Siege of Orra", "The Treaty of Sere", "caused"),
    ]
    for cause, effect, kind in chain:
        w.link_cause(events[cause].id, events[effect].id, kind=kind)

    for event, holder, label, account in ACCOUNTS:
        w.add_interpretation(label, event_id=events[event].id,
                             holder_id=out[holder].id, account=account)
    return out
