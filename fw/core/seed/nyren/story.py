"""The present: who is alive, what they want, and what is about to go wrong.

Four hundred and forty-seven years after the submission at Hadrin. Nyren's king is six
days dead, his succession is not settled, and the Grain Edict of 1094 has done to Orra
what two sieges could not — except that it has also emptied the basin's barns, because
grain nobody may ship is grain nobody buys.

The crisis is the one the geography has been threatening since 653. Nyren grows the
food and Merran owns the mouth of the river. Everything below is a consequence of that
single fact, which is the whole argument for building a world's ground before its plot.
"""

from __future__ import annotations

# The trade wheel: what each country has, and what it cannot do without. §41/§42 read
# these to answer where a city's bread comes from and what happens when it stops.
GOODS = {
    "Grain": ("food", "The Carth Basin", "very high"),
    "Flax": ("crop", "The Carth Basin", "high"),
    "Cattle": ("livestock", "The Carth Basin", "high"),
    "Timber": ("timber", "The Vardi Uplands", "very high"),
    "Iron": ("ore", "The Vardi Uplands", "high"),
    "Wool": ("livestock", "The Vardi Uplands", "high"),
    "Pitch": ("material", "The Selli North", "high"),
    "Amber": ("luxury", "The Selli North", "medium"),
    "Furs": ("luxury", "The Selli North", "medium"),
    "Wine": ("food", "The Talari South", "very high"),
    "Oil": ("food", "The Talari South", "high"),
    "Silver": ("ore", "The Eastern Mountains", "medium"),
    "Copper": ("ore", "The Eastern Mountains", "medium"),
    "Horses": ("livestock", "The Orri Steppe", "very high"),
    "Salt": ("mineral", "The Merran Coast", "high"),
    "Cloth": ("manufacture", "The Merran Coast", "very high"),
    "Ships": ("manufacture", "The Merran Coast", "high"),
}
NEEDS = {
    "The Merran Coast": ("Grain", "Timber", "Iron"),
    "The Carth Basin": ("Iron", "Salt", "Wine", "Silver"),
    "The Vardi Uplands": ("Grain", "Wine", "Salt"),
    "The Selli North": ("Grain", "Iron"),
    "The Talari South": ("Timber", "Grain", "Iron"),
    "The Eastern Mountains": ("Grain", "Cloth", "Wine"),
    "The Orri Steppe": ("Grain", "Timber", "Cloth"),
    "Nyreland": ("Grain", "Wine", "Oil"),
}
LANES = {
    "The Grain Run": (("Threeforks", "Orra"),
                      "Basin grain downriver to the estuary, and out to three seas — "
                      "when Hadrin permits it."),
    "The Timber Run": (("Kel Varro", "Calven"),
                       "Vardi timber to the shipyards. Merran's fleet is a Vardi "
                       "forest that has been moved."),
    "The Horse Road": (("Orrek", "Hadrin"),
                       "Orri horses to Nyren's cavalry, paid for in Carthi grain."),
    "The Wine Road": (("Talvere", "Sere"),
                      "Talari wine to the coast, and from there to every table that "
                      "wants to look expensive."),
}
# Where a place would starve rather than merely grumble.
DEPENDS = [
    ("Meret", "Threeforks", "Meret has never grown its own bread and has never needed "
                            "to. That is now a question rather than a boast."),
    ("Calven", "Kel Varro", "No Vardi timber, no Calven."),
    ("Hadrin", "Orrek", "Nyren's cavalry is Orri horseflesh and always has been."),
]


def build(w, made: dict) -> None:
    d = w.day
    from . import PRESENT_YEAR

    # ------------------------------------------------------- goods and trade
    goods = {}
    for name, (kind, where, level) in GOODS.items():
        item = w.add_entity("resource", name)
        goods[name] = item
        w.assert_fact(item, "note", value=kind)
        w.assert_fact(made[where], "produces", item, strength=level)
        w.assert_fact(made[where], "exports", item, strength=level)
    for region, wants in NEEDS.items():
        for want in wants:
            w.assert_fact(made[region], "imports", goods[want], strength="high")
    for name, (ends, summary) in LANES.items():
        lane = w.add_entity("trade_route", name, summary=summary)
        for end in ends:
            w.assert_fact(lane, "connects", made[end])
    for hungry, feeder, note in DEPENDS:
        w.assert_fact(made[hungry], "depends_on", made[feeder], note=note)

    # ----------------------------------------------------- the routable graph
    # Each stretch belongs to its road, and none of them predates either the road or
    # the younger of the two towns it joins — a road to a town that is not there yet is
    # the kind of thing `fw check` exists to catch, and did.
    founded = {"Nyrmark": 612, "Hadrin": 631, "Threeforks": 96, "Talvere": 122,
               "Calven": 231, "Orra": 168, "Veyra": 640, "Meret": 210, "Sere": 302,
               "Kel Varro": 74, "Orath": 61, "Sarth": 40, "Orrek": 180,
               "Nyrholt": 402}
    for road, a, b, length, medium, quality, ground, laid in (
        ("The King's Road", "Nyrmark", "Hadrin", 118, "road", 1.0, "plain", 634),
        ("The King's Road", "Hadrin", "Threeforks", 104, "road", 1.0, "plain", 634),
        ("The King's Road", "Threeforks", "Talvere", 148, "road", 0.8, "hill", 634),
        ("The Coast Road", "Calven", "Orra", 71, "road", 0.9, "coast", 288),
        ("The Coast Road", "Orra", "Veyra", 62, "road", 0.9, "coast", 288),
        ("The Coast Road", "Veyra", "Meret", 48, "road", 0.9, "coast", 288),
        ("The Coast Road", "Meret", "Sere", 58, "road", 0.8, "coast", 288),
        ("The Timber Road", "Kel Varro", "Calven", 88, "road", 0.6, "hill", 341),
        (None, "Orath", "Sarth", 61, "road", 0.4, "mountain", 61),
        (None, "Sarth", "Threeforks", 176, "road", 0.5, "mountain", 724),
        (None, "Orrek", "Orath", 141, "road", 0.4, "mountain", 180),
    ):
        w.add_route_segment(
            made[a].id, made[b].id, length, medium=medium, quality=quality,
            terrain=ground, entity_id=made[road].id if road else None,
            built_on=d(max(laid, founded[a], founded[b])),
            # A mountain road is a road for part of the year (§22).
            closed_seasons=["Deepwinter", "Fading"] if ground == "mountain" else [])
    # The river is the road the basin actually uses, and it freezes above Belcar.
    w.add_route_segment(made["Threeforks"].id, made["Orra"].id, 232, medium="river",
                        quality=1.0, terrain="water", closed_seasons=["Deepwinter"])
    w.add_route_segment(made["Nyrholt"].id, made["Nyrmark"].id, 210, medium="sea",
                        quality=0.8, terrain="water", closed_seasons=["Deepwinter"])

    # ----------------------------------------------------- houses and clans
    houses = {}
    for kind, name, seat, summary, since in (
        ("dynasty", "House Nyr", "Nyrholt",
         "The dynasty that unified Nyreland and then took a continent for want of "
         "anywhere else to put its younger sons.", 402),
        ("dynasty", "The Nyrid Line", "Hadrin",
         "House Nyr's continental branch, and since 806 the only one that matters.",
         653),
        ("house", "House Mereth", "Meret",
         "First among Meret's merchant families for six generations, which in Merran "
         "is as close to a crown as anyone gets.", 604),
        ("house", "House Carth", "Orath",
         "Carthain's royal line, and by their own reckoning the rightful kings of the "
         "whole basin.", 61),
        ("clan", "Clan Varro", "Kel Varro",
         "The Uplands' first clan. Sworn to Nyren, armed by somebody else.", 74),
        ("clan", "Clan Orrek", "Orrek",
         "The steppe confederation that currently answers for the horse trade.", 180),
    ):
        house = w.add_entity(kind, name, summary=summary, exists_from=d(since))
        houses[name] = house
        w.assert_fact(house, "based_in", made[seat], valid_from=d(since))
    w.assert_fact(houses["The Nyrid Line"], "cadet_branch_of", houses["House Nyr"],
                  valid_from=d(653))
    w.assert_fact(houses["Clan Varro"], "vassal_of", houses["The Nyrid Line"],
                  valid_from=d(721))
    w.assert_fact(houses["House Carth"], "vassal_of", houses["The Nyrid Line"],
                  valid_from=d(724), note="Tributary, and word-perfect about the "
                                          "difference.")
    w.assert_fact(houses["House Mereth"], "rival_of", houses["The Nyrid Line"])
    w.assert_fact(houses["The Nyrid Line"], "owes_debt_to", houses["House Mereth"],
                  strength="overwhelming",
                  note="Two sieges and a war, all of them borrowed from the people "
                       "they were fought against.")
    w.assert_fact(houses["House Mereth"], "wealth", value="beyond counting",
                  strength="very_high")
    w.assert_fact(houses["The Nyrid Line"], "military_strength", value="very high",
                  strength="very_high")
    w.assert_fact(houses["House Carth"], "prestige", value="very high",
                  strength="very_high")
    w.assert_fact(houses["House Carth"], "military_strength", value="negligible",
                  strength="low")

    # ------------------------------------------------------------ characters
    def person(name, *, born, died=None, gender, house=None, summary=""):
        p = w.add_entity("person", name, summary=summary, exists_from=d(born),
                         exists_to=d(*died) if isinstance(died, tuple)
                         else (d(died) if died else None))
        w.assert_fact(p, "gender", value=gender)
        w.assert_fact(p, "legitimacy", value="legitimate")
        if house:
            w.assert_fact(p, "member_of", houses[house])
        return p

    avaren3 = person("Avaren III", born=1013, died=1071, gender="male",
                     house="The Nyrid Line",
                     summary="Reigned thirty years and left two sons who did not speak.")
    avaren4 = person("King Avaren IV", born=1041, died=(PRESENT_YEAR, 5, 41),
                     gender="male", house="The Nyrid Line",
                     summary="Signed the Grain Edict, and died six days later with the "
                             "harvest still in the barns.")
    edrec = person("Prince Edrec", born=1044, died=1092, gender="male",
                   house="The Nyrid Line",
                   summary="Avaren's younger brother. Married south, and was never "
                           "forgiven for it.")
    ilva = person("Queen Ilva", born=1049, gender="female", house="The Nyrid Line",
                  summary="Avaren's widow. Has held the council together for six days "
                          "and does not intend to hold it much longer.")
    hadren = person("Prince Hadren", born=1072, gender="male", house="The Nyrid Line",
                    summary="Heir apparent, and certain that the answer to Orra is a "
                            "third siege.")
    sereth = person("Lady Sereth", born=1076, gender="female", house="House Mereth",
                    summary="Edrec's daughter, married into Meret. Second in line to "
                            "Nyren, and living in the city Nyren means to take.")
    toran = person("Toran Mereth", born=1051, gender="male", house="House Mereth",
                   summary="First Councillor of Meret. Holds Nyren's debt and his own "
                           "niece by marriage.")
    corvan = person("King Corvan of Carthain", born=1058, gender="male",
                    house="House Carth",
                    summary="The oldest crown on the continent, three hundred soldiers, "
                            "and an archive nobody has read in four centuries.")
    belen = person("Archivist Belen", born=1063, gender="female", house="House Carth",
                   summary="Keeper of the Orath rolls. Has read it.")
    varro = person("Duke Varro", born=1055, gender="male", house="Clan Varro",
                   summary="Sworn to Hadrin, and unable to explain where the Uplands' "
                           "new weapons came from.")
    aska = person("Aska of the Orrek", born=1061, gender="female", house="Clan Orrek",
                  summary="Sells horses to Nyren and hears everything Merran pays her "
                          "to hear.")
    hallen = person("Master Hallen", born=1059, gender="male",
                    summary="Nyren's surveyor at Orra: Carthi born, Nyri trained, and "
                            "trusted by neither.")

    w.assert_fact(avaren3, "parent_of", avaren4)
    w.assert_fact(avaren3, "parent_of", edrec)
    w.assert_fact(avaren4, "married_to", ilva, valid_from=d(1070))
    w.assert_fact(avaren4, "parent_of", hadren)
    w.assert_fact(ilva, "parent_of", hadren)
    w.assert_fact(edrec, "parent_of", sereth)
    w.assert_fact(toran, "parent_of", sereth, note="By marriage; Sereth is his son's "
                                                   "wife and his best card.")

    # §5 the web, §4 what each of them is actually after
    w.assert_fact(hadren, "trusts", toran, strength="distrusts")
    w.assert_fact(toran, "respects", hadren, strength="low")
    w.assert_fact(ilva, "trusts", sereth, strength="suspicious")
    w.assert_fact(belen, "fears", corvan, note="Because of what she will have to tell "
                                               "him, and what he will do with it.")
    w.assert_fact(varro, "owes_debt_to", toran, secrecy="secret",
                  note="Paid in Meret silver, through three hands.")
    w.assert_fact(aska, "trusts", hadren, strength="wary")
    w.assert_fact(hallen, "resents", hadren,
                  note="Twenty years measuring the estuary, and asked only ever how "
                       "fast an army could cross it.")
    w.assert_fact(hadren, "surface_goal", value="To be crowned and to take Orra")
    w.assert_fact(hadren, "private_goal", value="To be the king who finished it")
    w.assert_fact(hadren, "fear", value="That the council prefers his cousin")
    w.assert_fact(sereth, "surface_goal", value="To keep Meret out of a war of succession")
    w.assert_fact(sereth, "private_goal", value="To be named heir without leaving Meret")
    w.assert_fact(toran, "private_goal", value="A Nyren crown that owes Meret everything")
    w.assert_fact(ilva, "private_goal", value="To see her son crowned before the "
                                              "council finds a reason not to")
    w.assert_fact(corvan, "private_goal", value="To be asked, once, what Carthain thinks")
    w.assert_fact(belen, "surface_goal", value="To finish the calendar of the rolls")
    w.assert_fact(varro, "private_goal", value="An Uplands that pays its own levy and "
                                               "nobody else's")
    w.assert_fact(aska, "private_goal", value="To sell to whoever is still buying in "
                                              "the spring")

    # ---------------------------------------------------------- §8 the titles
    crown = w.add_title("King of Nyren", rank=100, territory_id=made["Nyren"].id,
                        succession_law="male_preference_primogeniture",
                        dynasty_root_id=avaren3.id, created_on=d(653))
    w.grant_title(crown.id, avaren3.id, from_day=d(1041), to_day=d(1071))
    w.grant_title(crown.id, avaren4.id, from_day=d(1071),
                  to_day=d(PRESENT_YEAR, 5, 41))
    carth_crown = w.add_title("King of Carthain", rank=90,
                              territory_id=made["Carthain"].id,
                              succession_law="male_only_primogeniture",
                              dynasty_root_id=corvan.id, created_on=d(671))
    w.grant_title(carth_crown.id, corvan.id, from_day=d(1089))
    estuary = w.add_title("Warden of the Estuary", rank=40, territory_id=made["Orra"].id,
                          succession_law="appointed", created_on=d(806))
    w.grant_title(estuary.id, hallen.id, from_day=d(1080))

    # ------------------------------------------------ §6 the secret itself
    secret = w.add_secret(
        "The substitution at Threeforks",
        truth="The child of the Marriage at Threeforks died in its first winter. The "
              "boy who inherited both claims in 653 was a Nyri cousin, presented as "
              "the heir by agreement of four men. Nyren's title to the Carth Basin "
              "rests on that agreement, and the record of it is in the Orath rolls.",
        about_id=made["Nyren"].id, severity="critical")
    w.set_knowledge(belen.id, secret.id, "knows", acquired_on=d(1099),
                    note="Found while cataloguing. Has told nobody, and is running out "
                         "of reasons.")
    w.set_knowledge(corvan.id, secret.id, "suspects", acquired_on=d(1099),
                    note="His archivist has stopped meeting his eye.")
    w.set_knowledge(hadren.id, secret.id, "unaware")
    w.set_knowledge(ilva.id, secret.id, "misinformed",
                    note="Was told a version of it once, as a slander, and dismissed it.")
    w.set_knowledge(toran.id, secret.id, "suspects", acquired_on=d(1096),
                    note="Meret has been buying Carthi manuscripts for four years and "
                         "not saying why.")
    w.set_knowledge(sereth.id, secret.id, "unaware")
    w.set_knowledge(corvan.id, secret.id, "knows", about_observer_id=belen.id,
                    acquired_on=d(1100), note="He knows that she knows. Neither has "
                                              "said the word aloud.")

    # §94: the same man, called different things, which is the shortest statement of
    # where a country stands.
    for holder, label, account in (
        ("Nyren", "His Grace the Prince",
         "Avaren's son and heir, and the next King of Nyren."),
        ("Merran", "The claimant at Hadrin",
         "One of two, and the one with the larger army rather than the better right."),
        ("Carthain", "The Nyri boy",
         "Fourth of a line that has never once shown us its title."),
    ):
        w.add_interpretation(label, entity_id=hadren.id, holder_id=made[holder].id,
                             account=account)

    # ------------------------------------------------- the present crisis
    w.add_event(
        "The death of King Avaren IV", type_key="event",
        summary="Died at Hadrin six days after signing the Grain Edict, the succession "
                "unsettled and the barns full.",
        start_day=d(PRESENT_YEAR, 5, 41), location_id=made["Hadrin"].id,
        participants=[(avaren4.id, "subject"), (ilva.id, "witness")])
    w.add_event(
        "The price of grain at Orra", type_key="event",
        summary="Four seasons of the Edict. Orra's warehouses full, the basin's price "
                "collapsed, and both sides blaming the other in public.",
        start_day=d(1095), location_id=made["Orra"].id,
        participants=[(toran.id, "affected"), (hallen.id, "witness")])

    # --------------------------------------------- §44 the manuscript layer
    novel = w.add_work("The Mouth of the River", kind="novel")
    chapter = w.add_chapter(novel, "Six Days", position=1)
    w.add_scene(
        "The council at Hadrin", chapter_id=chapter, position=1,
        day=d(PRESENT_YEAR, 5, 44), location_id=made["Hadrin"].id, pov_id=ilva.id,
        objective="Ilva must have Hadren acclaimed before the council remembers Sereth.",
        conflict="Half the room owes Meret money and the other half wants the war.",
        participants=[ilva.id, hadren.id, varro.id, hallen.id])
    w.add_scene(
        "What Belen found", chapter_id=chapter, position=2,
        day=d(PRESENT_YEAR, 5, 46), location_id=made["Orath"].id, pov_id=belen.id,
        objective="Belen means to put the roll in front of Corvan and be rid of it.",
        conflict="He has wanted this his whole life and she has watched him want it.",
        participants=[belen.id, corvan.id])

    # ------------------------------------------------------- §80 snapshots
    w.add_snapshot("Before the Conquest", d(611))
    w.add_snapshot("The submission at Hadrin", d(653, 6, 3))
    w.add_snapshot("After the Treaty of Sere", d(807))
    w.add_snapshot("The Grain Edict", d(1094, 2, 30))
    w.add_snapshot("Current manuscript date", d(PRESENT_YEAR, 5, 44))
