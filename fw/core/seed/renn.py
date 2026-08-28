"""The Kingdom of Renn — the worked example the brief asks for (spec §115).

§115 requires a demonstration world containing at minimum one kingdom, three regions, six
settlements, three noble houses, one royal dynasty, eight characters, one disputed
inheritance, two roads, one major river, several resources, two trade routes, one
historical war, one secret and one active political crisis.

Almost all of it is named in the brief's own running examples — Greyhaven, Rennford, Houses
Marr, Veyne and Orren, the River Renn, King Aldren, Prince Oren, Mara, Edric, Tomas, Queen
Sera, the Battle of Red Ford — so this world is assembled from those rather than invented
alongside them. The disputed inheritance, the secret and the political crisis are one and
the same thing: Prince Oren's parentage.

The genealogy is arranged to reproduce §8's stated succession exactly, and §57's stated
example of conflicting parentage exactly:

    §8:  1. Prince Oren  2. Lady Elia  3. Lord Caros  4. Lady Mara
         and, if Oren is declared illegitimate: 1. Elia  2. Caros  3. Mara
    §57: Parent of Prince Oren: King Aldren — publicly believed
                                Lord Corren — canonical secret

Those two fit together: Old King Renn fathered Aldren and Corren; Aldren's legal children
are Oren and Elia; Corren's are Caros and Mara — and Oren's *biological* father is Corren,
which is both the secret and the reason the inheritance is disputed.

This world doubles as the integration-test fixture, so the tests exercise the same world a
new user first opens.
"""

from __future__ import annotations

from fw.core.calendar.kernel import Calendar, Era, Month, Season
from fw.core.ids import new_id
from fw.core.world import World

# The Rennish calendar: five months, a 355-day year, ten-day weeks. Deliberately not
# Gregorian — if the engines only work on an Earth calendar, §60 is not being honoured.
RENNISH = Calendar(
    name="Rennish",
    months=(
        Month("Frostwane", 61), Month("Seedfall", 73), Month("Highsun", 80),
        Month("Harvestide", 73), Month("Darkening", 68),
    ),
    weekdays=("Kingsday", "Mareday", "Orrenday", "Veyneday", "Marrday",
              "Fordday", "Restday", "Hallow", "Emberday", "Lastday"),
    leap_every=4,
    # Three ages, two of which are the ordinary kind — a name appended to the year — and
    # one that shows what §3's dividers are really for: the Long Dark counts *backwards*
    # toward the founding, exactly as BC does, so 120 BD is older than 40 BD.
    eras=(Era("The Long Dark", "BD", end_year=0, counts_backward=True),
          Era("Age of Founding", "AF", 1, 199),
          Era("Age of Kings", "AK", 200)),
    seasons=(Season("Deepwinter", 1), Season("Greening", 62), Season("Highsummer", 135),
             Season("Harvest", 215), Season("Fading", 288)),
)

# The story's present. Everything the dashboard shows defaults to this day.
PRESENT_YEAR = 241


def seed_renn(path: str = ":memory:") -> World:
    """Build the example world and return it."""
    w = World.create(path, name="The Kingdom of Renn",
                     description="A low-fantasy realm in the year of a disputed crown.",
                     calendar=RENNISH)
    d = w.day

    # ------------------------------------------------------------------ realm
    renn = w.add_entity("realm", "The Kingdom of Renn",
                        summary="A cold northern kingdom of iron, grain and long memory.",
                        exists_from=d(88))
    w.assert_fact(renn, "government", value="feudal monarchy")

    # ------------------------------------------------------------- geography
    northmarch = w.add_entity("region", "The Northmarch",
                              summary="Mountainous, iron-rich, forested, and poor in grain. "
                                      "One pass carries all traffic to the interior.")
    vale = w.add_entity("region", "The Vale of Renn",
                        summary="The kingdom's breadbasket: broad river plains and good soil.")
    reach = w.add_entity("region", "The Salt Reach",
                         summary="A low windward coast of fisheries, salt pans and harbours.")

    for region in (northmarch, vale, reach):
        w.assert_fact(region, "located_in", renn)

    w.assert_fact(northmarch, "terrain", value="mountains and forest")
    w.assert_fact(northmarch, "climate", value="cold, heavy snow in Darkening")
    w.assert_fact(northmarch, "population", value="41000")
    w.assert_fact(northmarch, "defensibility", value="high", strength="high")
    w.assert_fact(vale, "terrain", value="river plain")
    w.assert_fact(vale, "climate", value="temperate")
    w.assert_fact(vale, "population", value="120000")
    w.assert_fact(reach, "terrain", value="coast and marsh")
    w.assert_fact(reach, "population", value="58000")
    w.assert_fact(northmarch, "borders", vale)
    w.assert_fact(vale, "borders", reach)

    # ------------------------------------------------------------ settlements
    greyhaven = w.add_entity(
        "settlement", "Greyhaven",
        summary="The Northmarch's only deep harbour, and the mouth of the iron trade.",
        exists_from=d(120))
    rennford = w.add_entity(
        "settlement", "Rennford",
        summary="The capital: a walled city at the last ford of the River Renn.",
        exists_from=d(94))
    blackmere = w.add_entity(
        "settlement", "Blackmere",
        summary="A salt and fishing port, and Greyhaven's rival for the coastal trade.",
        exists_from=d(140))
    millbrook = w.add_entity(
        "settlement", "Millbrook",
        summary="A market town of mills and granaries in the Vale.", exists_from=d(150))
    northwatch = w.add_entity(
        "settlement", "Northwatch",
        summary="A fortress guarding the only pass out of the Northmarch.",
        exists_from=d(163))
    redford = w.add_entity(
        "settlement", "Red Ford",
        summary="A river crossing and the site of the war's decisive battle.",
        exists_from=d(131))

    settlements = {
        greyhaven: (northmarch, "port", "7400"),
        rennford: (vale, "capital", "31000"),
        blackmere: (reach, "port", "6100"),
        millbrook: (vale, "market town", "3300"),
        northwatch: (northmarch, "fortress", "1800"),
        redford: (vale, "town", "2600"),
    }
    for town, (region, kind, population) in settlements.items():
        w.assert_fact(town, "located_in", region)
        w.assert_fact(town, "settlement_type", value=kind)
        w.assert_fact(town, "population", value=population)
    w.assert_fact(rennford, "capital_of", renn)

    # ---------------------------------------------------------------- water
    river = w.add_entity("waterway", "The River Renn",
                         summary="Navigable from Rennford to the sea; the kingdom's spine.")
    for place in (rennford, redford, millbrook):
        w.assert_fact(river, "flows_through", place)
    w.assert_fact(river, "located_in", vale)

    # ---------------------------------------------------------------- roads
    iron_road = w.add_entity("road", "The Iron Road",
                             summary="Greyhaven to Rennford by way of Red Ford and Millbrook.",
                             exists_from=d(148))
    pass_road = w.add_entity("road", "The Northwatch Pass Road",
                             summary="The high road through the pass. Closed by snow each "
                                     "Darkening.",
                             exists_from=d(202))
    w.assert_fact(iron_road, "connects", greyhaven)
    w.assert_fact(iron_road, "connects", rennford)
    w.assert_fact(pass_road, "connects", northwatch)

    # The routable network behind §22's travel questions.
    w.add_route_segment(greyhaven.id, redford.id, 42, entity_id=iron_road.id,
                        quality=1.0, terrain="plain", built_on=d(148))
    w.add_route_segment(redford.id, millbrook.id, 31, entity_id=iron_road.id,
                        quality=0.8, terrain="hill", built_on=d(148))
    w.add_route_segment(millbrook.id, rennford.id, 41, entity_id=iron_road.id,
                        quality=1.0, terrain="plain", built_on=d(148))
    w.add_route_segment(greyhaven.id, northwatch.id, 95, entity_id=pass_road.id,
                        quality=0.5, terrain="mountain", built_on=d(202),
                        # Closures are tested against the *season*, so the month the prose
                        # names — Darkening — has to be written as the season it falls in.
                        # "Darkening" here closed the pass on no day of any year.
                        closed_seasons=["Fading", "Deepwinter"])
    w.add_route_segment(northwatch.id, rennford.id, 70, entity_id=pass_road.id,
                        quality=0.6, terrain="hill", built_on=d(202))
    w.add_route_segment(rennford.id, blackmere.id, 55, quality=0.9, terrain="plain")
    # The river is a route in its own right, and freezes.
    w.add_route_segment(rennford.id, greyhaven.id, 130, medium="river", quality=1.0,
                        terrain="water", closed_seasons=["Deepwinter"])

    # ------------------------------------------------------------- resources
    resources = {
        "Iron": ("ore", northmarch, "high"),
        "Timber": ("timber", northmarch, "high"),
        "Grain": ("grain", vale, "high"),
        "Salt": ("mineral", reach, "medium"),
        "Wool": ("livestock", vale, "medium"),
        "Fish": ("food", reach, "high"),
    }
    resource_entities = {}
    for name, (kind, region, level) in resources.items():
        r = w.add_entity("resource", name)
        resource_entities[name] = r
        w.assert_fact(r, "note", value=kind)
        w.assert_fact(region, "produces", r, strength=level)

    # Greyhaven feeds itself on imported grain — the dependency §42 asks about.
    w.assert_fact(greyhaven, "imports", resource_entities["Grain"], strength="high")
    w.assert_fact(vale, "exports", resource_entities["Grain"], strength="high")
    w.assert_fact(greyhaven, "exports", resource_entities["Iron"], strength="high")
    w.assert_fact(greyhaven, "depends_on", millbrook,
                  note="Greyhaven's grain arrives from the Vale by the Iron Road.")

    # ---------------------------------------------------------- trade routes
    iron_run = w.add_entity("trade_route", "The Iron Run",
                            summary="Iron and timber south from Greyhaven; grain north in return.")
    salt_run = w.add_entity("trade_route", "The Salt Run",
                            summary="Salt and fish from Blackmere upriver to Rennford.")
    w.assert_fact(iron_run, "connects", greyhaven)
    w.assert_fact(iron_run, "connects", rennford)
    w.assert_fact(salt_run, "connects", blackmere)
    w.assert_fact(salt_run, "connects", rennford)

    # -------------------------------------------------------------- houses
    crown_house = w.add_entity("dynasty", "House Renn",
                               summary="The royal dynasty, kings since the Founding.",
                               exists_from=d(88))
    marr = w.add_entity("house", "House Marr",
                        summary="Lords of the Northmarch. Iron-rich, grain-poor, proud.",
                        exists_from=d(150))
    veyne = w.add_entity("house", "House Veyne",
                         summary="The Vale's great house, and the Crown's chief creditor.",
                         exists_from=d(112))
    orren = w.add_entity("house", "House Orren",
                         summary="Masters of the Salt Reach and of Blackmere's harbour.",
                         exists_from=d(133))

    w.assert_fact(marr, "motto", value="Iron endures")
    w.assert_fact(veyne, "motto", value="The debt is remembered")
    w.assert_fact(orren, "motto", value="Salt and patience")
    w.assert_fact(marr, "military_strength", value="high", strength="high")
    w.assert_fact(veyne, "wealth", value="very high", strength="very_high")
    w.assert_fact(orren, "wealth", value="high", strength="high")

    # §10 feudal chain, and §11's four distinct authorities over one place.
    w.assert_fact(veyne, "vassal_of", crown_house, valid_from=d(150))
    w.assert_fact(marr, "vassal_of", veyne, valid_from=d(150))
    w.assert_fact(orren, "vassal_of", crown_house, valid_from=d(150))

    # Where the great houses actually sit, so a region can answer "who is here".
    w.assert_fact(marr, "based_in", northwatch, valid_from=d(150))
    w.assert_fact(veyne, "based_in", rennford, valid_from=d(112))
    w.assert_fact(orren, "based_in", blackmere, valid_from=d(133))

    # ------------------------------------------------- minor houses and groups
    # A world is not only its great houses. The lesser houses under a banner, the
    # guilds working a town and the orders ranging across a march are the texture a
    # writer reaches for, and each is reachable from its region and from its liege.
    dray = w.add_entity("house", "House Dray",
                        summary="Sworn to Marr, and quietly in debt to Veyne.",
                        exists_from=d(171))
    pell = w.add_entity("house", "House Pell",
                        summary="A cadet branch of Veyne, seated at the Millbrook mills.",
                        exists_from=d(186))
    w.assert_fact(dray, "sworn_to", marr, valid_from=d(171))
    w.assert_fact(dray, "subgroup_of", marr, valid_from=d(171))
    w.assert_fact(dray, "based_in", northwatch, valid_from=d(171))
    w.assert_fact(pell, "cadet_branch_of", veyne, valid_from=d(186))
    w.assert_fact(pell, "subgroup_of", veyne, valid_from=d(186))
    w.assert_fact(pell, "based_in", millbrook, valid_from=d(186))

    ironmongers = w.add_entity("guild", "The Ironmongers of Red Ford",
                               summary="They price the Northmarch's iron, and know it.",
                               exists_from=d(198))
    w.assert_fact(ironmongers, "based_in", redford, valid_from=d(198))
    w.assert_fact(ironmongers, "active_in", northmarch, valid_from=d(198))

    ford_order = w.add_entity("order", "The Order of the Ford",
                              summary="Sworn to keep the crossings open in any war.",
                              exists_from=d(160))
    w.assert_fact(ford_order, "based_in", redford, valid_from=d(160))
    w.assert_fact(ford_order, "active_in", vale, valid_from=d(160))

    hillfolk = w.add_entity("tribe", "The Hillfolk",
                            summary="Older than the kingdom, and unimpressed by it.",
                            exists_from=d(1))
    w.assert_fact(hillfolk, "active_in", northmarch, valid_from=d(1))

    free_company = w.add_entity("company", "The Grey Spears",
                                summary="A free company, currently unpaid.",
                                exists_from=d(231))
    w.assert_fact(free_company, "based_in", greyhaven, valid_from=d(238))
    w.assert_fact(free_company, "sworn_to", orren, valid_from=d(238))

    # Greyhaven's tangled control, exactly as §11 describes it: House Marr owns it in law,
    # House Veyne runs it day to day, the Crown taxes it, and House Orren claims it.
    w.assert_fact(marr, "legally_owns", greyhaven, valid_from=d(150))
    w.assert_fact(veyne, "administers", greyhaven, valid_from=d(228))
    w.assert_fact(crown_house, "taxes", greyhaven, valid_from=d(150))
    w.assert_fact(orren, "claims", greyhaven, valid_from=d(238),
                  note="Advanced on the strength of Lady Mara's marriage settlement.")
    w.assert_fact(marr, "legally_owns", northmarch, valid_from=d(150))
    w.assert_fact(marr, "administers", northwatch, valid_from=d(202))
    w.assert_fact(veyne, "legally_owns", vale, valid_from=d(150))
    w.assert_fact(orren, "legally_owns", reach, valid_from=d(150))
    w.assert_fact(veyne, "legally_owns", millbrook, valid_from=d(160))
    w.assert_fact(orren, "legally_owns", blackmere, valid_from=d(160))
    w.assert_fact(crown_house, "legally_owns", rennford, valid_from=d(94))

    # ------------------------------------------------------------ characters
    def when(spec):
        """Accept a bare year or a (year, month, day) tuple."""
        return d(*spec) if isinstance(spec, tuple) else d(spec)

    def person(name, *, born, died=None, gender, house=None, summary="",
               legitimacy="legitimate"):
        p = w.add_entity("person", name, summary=summary,
                         exists_from=when(born),
                         exists_to=when(died) if died is not None else None)
        w.assert_fact(p, "gender", value=gender)
        w.assert_fact(p, "legitimacy", value=legitimacy)
        if house is not None:
            w.assert_fact(p, "member_of", house)
        return p

    old_king = person("Old King Renn", born=140, died=201, gender="male", house=crown_house,
                      summary="Fathered two sons and left a kingdom that outlived them both.")
    aldren = person("King Aldren", born=170, died=(240, 5, 61), gender="male",
                    house=crown_house,
                    summary="Reigned forty years and died without settling the question "
                            "of his heir.")
    corren = person("Lord Corren", born=174, died=235, gender="male", house=crown_house,
                    summary="The king's younger brother. Died before the crisis he caused.")
    sera = person("Queen Sera", born=182, gender="female", house=crown_house,
                  summary="Aldren's queen. Knows more than she has ever said aloud.")
    oren = person("Prince Oren", born=210, gender="male", house=crown_house,
                  summary="Heir apparent, and the only person at court who believes his "
                          "parentage is not in question.")
    elia = person("Lady Elia", born=212, gender="female", house=crown_house,
                  summary="Aldren's daughter. Second in line, and counting.")
    caros = person("Lord Caros", born=215, gender="male", house=crown_house,
                   summary="Corren's son, raised in the Vale under Veyne's eye.")
    mara = person("Lady Mara", born=218, gender="female", house=crown_house,
                  summary="Corren's daughter, married into House Orren. Keeps the "
                          "kingdom's most dangerous secret.")
    edric = person("Edric", born=209, gender="male", house=marr,
                   summary="A Marr captain. His brother was executed eleven days ago.")
    tomas = person("Tomas", born=196, gender="male", house=veyne,
                   summary="Veyne's steward and the Crown's principal creditor's voice.")

    # ---- §7 parentage, including the split the whole plot turns on -------
    # Old King Renn fathered both Aldren and Corren.
    w.assert_fact(old_king, "parent_of", aldren)
    w.assert_fact(old_king, "parent_of", corren)
    w.assert_fact(aldren, "married_to", sera, valid_from=d(206))

    # Elia is Aldren's by blood and by law.
    w.assert_fact(aldren, "parent_of", elia)
    w.assert_fact(sera, "parent_of", elia)

    # Oren is Aldren's son in law and in public belief -- and Corren's by blood.
    # §57's worked example, stored as two facts of different confidence and secrecy
    # rather than as one field that has to be either true or false.
    w.assert_fact(aldren, "legal_parent_of", oren, confidence="canon",
                  note="Publicly believed, and legally unchallenged.")
    w.assert_fact(sera, "parent_of", oren)
    w.assert_fact(corren, "parent_of", oren, confidence="canon", secrecy="deep_secret",
                  note="The truth. Known to four people, two of whom are dead.")

    w.assert_fact(corren, "parent_of", caros)
    w.assert_fact(corren, "parent_of", mara)

    # ------------------------------------------------- §5 relationship web
    w.assert_fact(mara, "feels_about", edric, strength="loves", secrecy="secret",
                  note="Never spoken, and never safe to speak.")
    w.assert_fact(tomas, "trusts", edric, strength="distrusts")
    w.assert_fact(edric, "owes_debt_to", sera, note="A debt of coin, and one of silence.")
    w.assert_fact(sera, "trusts", mara, strength="suspicious",
                  note="Sera suspects Mara of treason, and is not wrong to.")
    w.assert_fact(oren, "trusts", mara, strength="deeply_trusts",
                  note="Which is the cruellest fact in the kingdom.")
    w.assert_fact(marr, "rival_of", orren)
    w.assert_fact(veyne, "allied_with", crown_house)
    w.assert_fact(crown_house, "owes_debt_to", veyne, strength="overwhelming",
                  note="The Crown has borrowed against the next three harvests.")

    # §4 motivation model
    w.assert_fact(mara, "surface_goal", value="To see the succession settled peacefully")
    w.assert_fact(mara, "private_goal", value="To see Oren crowned before the truth surfaces")
    w.assert_fact(mara, "fear", value="That Sera will force her to testify")
    w.assert_fact(edric, "surface_goal", value="To serve House Marr faithfully")
    w.assert_fact(edric, "private_goal", value="To see Oren discredited and his brother avenged")
    w.assert_fact(sera, "private_goal", value="To find the leak and close it")
    w.assert_fact(tomas, "private_goal", value="To avoid a civil war that would void the debt")
    w.assert_fact(oren, "surface_goal", value="To be crowned as his father's heir")
    w.assert_fact(elia, "private_goal", value="To be taken seriously as a claimant")
    w.assert_fact(caros, "private_goal", value="To be granted the Northmarch for his silence")

    # -------------------------------------------------------- §8 the titles
    crown_title = w.add_title("King of Renn", rank=100, territory_id=renn.id,
                              succession_law="male_preference_primogeniture",
                              dynasty_root_id=old_king.id, created_on=d(88))
    w.grant_title(crown_title.id, old_king.id, from_day=d(160), to_day=d(201))
    w.grant_title(crown_title.id, aldren.id, from_day=d(201), to_day=d(240, 5, 61))

    # Two lesser titles, deliberately under different succession laws so the same family
    # produces different heirs depending on what is being inherited (§8).
    greyhaven_title = w.add_title("Lord of Greyhaven", rank=30, territory_id=greyhaven.id,
                                  succession_law="male_preference_primogeniture",
                                  dynasty_root_id=old_king.id, created_on=d(150))
    w.grant_title(greyhaven_title.id, corren.id, from_day=d(220), to_day=d(235))
    w.grant_title(greyhaven_title.id, caros.id, from_day=d(235))

    northmarch_title = w.add_title("Warden of the Northmarch", rank=50,
                                   territory_id=northmarch.id,
                                   succession_law="male_only_primogeniture",
                                   dynasty_root_id=old_king.id, created_on=d(150))
    w.grant_title(northmarch_title.id, aldren.id, from_day=d(201), to_day=d(240, 5, 61))

    # ---------------------------------------------------- §31 history & war
    red_war = w.add_event(
        "The Red War", type_key="war",
        summary="Three years of fighting between the Crown and the northern houses over "
                "the iron tolls.",
        start_day=d(226), end_day=d(229), location_id=northmarch.id,
        participants=[(marr.id, "belligerent"), (crown_house.id, "belligerent"),
                      (veyne.id, "belligerent")])
    red_ford = w.add_event(
        "The Battle of Red Ford", type_key="battle",
        summary="The war's decisive engagement, fought at the ford in a single morning.",
        start_day=d(229, 2, 12), location_id=redford.id,
        participants=[(aldren.id, "commander"), (corren.id, "commander"),
                      (edric.id, "participant")])
    tolls = w.add_event(
        "The Iron Toll Edict", type_key="event",
        summary="The Crown doubled the tolls on the Iron Road to service its debts.",
        start_day=d(225), location_id=rennford.id,
        participants=[(crown_house.id, "author"), (veyne.id, "beneficiary")])
    treaty = w.add_event(
        "The Peace of Millbrook", type_key="treaty",
        summary="Ended the Red War. House Marr kept Greyhaven; House Veyne gained its "
                "administration.",
        start_day=d(229, 4, 3), location_id=millbrook.id,
        participants=[(marr.id, "signatory"), (crown_house.id, "signatory"),
                      (veyne.id, "signatory")])
    w.add_event(
        "The execution of Edric's brother", type_key="event",
        summary="Hanged at Rennford for a theft he did not commit.",
        start_day=d(PRESENT_YEAR, 1, 20), location_id=rennford.id,
        participants=[(edric.id, "bereaved"), (mara.id, "authorised")])
    death_of_aldren = w.add_event(
        "The death of King Aldren", type_key="event",
        summary="Died in his sleep at Rennford, the succession unsettled.",
        start_day=d(240, 5, 61), location_id=rennford.id,
        participants=[(aldren.id, "subject"), (sera.id, "witness")])

    # §32 causality: the chain that produced the present crisis
    w.link_cause(tolls.id, red_war.id, note="The tolls were the northern houses' casus belli.")
    w.link_cause(red_war.id, red_ford.id)
    w.link_cause(red_ford.id, treaty.id)
    w.link_cause(treaty.id, death_of_aldren.id, kind="contributed_to",
                 note="The peace left the inheritance question deliberately unanswered.")

    # §33 the same battle, told three ways
    for holder, label, account in (
        (crown_house.id, "The Crown's account",
         "A hard-won victory that preserved the realm's unity."),
        (marr.id, "The northern account",
         "A massacre of men who had already asked for terms."),
        (None, "The clerical account",
         "A judgement upon a king who taxed what he had not built."),
    ):
        w.db.insert("interpretation", {
            "id": new_id(), "event_id": red_ford.id, "holder_id": holder,
            "label": label, "account": account,
        })

    # ------------------------------------------------- §6 the secret itself
    secret = w.add_secret(
        "Prince Oren's parentage",
        truth="Prince Oren is Lord Corren's son, not King Aldren's. He is illegitimate "
              "and has no claim to the throne of Renn.",
        about_id=oren.id, severity="critical")

    # The five distinct stances the brief asks for, and the second-order layer.
    w.set_knowledge(mara.id, secret.id, "knows", acquired_on=d(232),
                    note="Told by Corren on his deathbed.")
    w.set_knowledge(sera.id, secret.id, "knows", acquired_on=d(210),
                    note="She was there. She has never said so.")
    w.set_knowledge(oren.id, secret.id, "misinformed", note="Believes himself legitimate.")
    w.set_knowledge(tomas.id, secret.id, "suspects", acquired_on=d(238),
                    note="Has done the arithmetic on Aldren's absences and said nothing.")
    w.set_knowledge(edric.id, secret.id, "suspects", acquired_on=d(240))
    w.set_knowledge(elia.id, secret.id, "unaware")
    w.set_knowledge(caros.id, secret.id, "knows", acquired_on=d(235),
                    note="Corren told him too, and told him what it was worth.")
    # §6's second-order layer: Sera knows that Mara knows.
    w.set_knowledge(sera.id, secret.id, "knows", about_observer_id=mara.id,
                    acquired_on=d(239),
                    note="Which is why Mara has not left the capital since Harvestide.")

    # --------------------------------------------- §44 the manuscript layer
    novel = w.add_work("The Iron Crown", kind="novel")
    chapter = w.add_chapter(novel, "Winter Feast", position=1)
    w.add_scene(
        "The Winter Feast at Greyhaven", chapter_id=chapter, position=1,
        day=d(PRESENT_YEAR, 1, 31), location_id=greyhaven.id, pov_id=mara.id,
        objective="Mara must win Tomas to Oren's cause before the council sits.",
        conflict="Everyone at the table wants something incompatible from everyone else.",
        outcome="",
        participants=[mara.id, tomas.id, edric.id, sera.id, oren.id],
    )
    w.add_scene(
        "The Reading of the Will", chapter_id=chapter, position=2,
        day=d(PRESENT_YEAR, 2, 6), location_id=rennford.id, pov_id=elia.id,
        objective="Elia means to hear her own name read out.",
        conflict="The will is ambiguous, and three people in the room know why.",
        participants=[elia.id, oren.id, sera.id, tomas.id],
    )

    # ------------------------------------------------------- §80 snapshots
    w.add_snapshot("Before the Red War", d(225))
    w.add_snapshot("After the Peace of Millbrook", d(230))
    w.add_snapshot("The death of King Aldren", d(240, 5, 61))
    w.add_snapshot("Current manuscript date", d(PRESENT_YEAR, 1, 31))

    # ------------------------------------------------------------- geometry
    # World coordinates, not a real projection: fictional maps have no CRS (§34).
    places = {
        greyhaven: (120, 640), rennford: (430, 300), blackmere: (700, 210),
        millbrook: (330, 350), northwatch: (300, 620), redford: (250, 430),
    }
    for entity, (x, y) in places.items():
        w.add_geometry(entity.id, "point", [x, y], layer="settlements")

    w.add_geometry(northmarch.id, "polygon",
                   [[[40, 500], [420, 520], [400, 760], [60, 780], [40, 500]]],
                   layer="regions", style={"fill": "#5b6a72"})
    w.add_geometry(vale.id, "polygon",
                   [[[180, 220], [560, 240], [540, 520], [200, 500], [180, 220]]],
                   layer="regions", style={"fill": "#6f7c4e"})
    w.add_geometry(reach.id, "polygon",
                   [[[560, 120], [820, 140], [800, 340], [560, 300], [560, 120]]],
                   layer="regions", style={"fill": "#7c7358"})
    w.add_geometry(river.id, "line",
                   [[430, 300], [330, 350], [250, 430], [180, 560], [120, 640]],
                   layer="waterways", style={"stroke": "#4a7fa5"})
    w.add_geometry(iron_road.id, "line",
                   [[120, 640], [250, 430], [330, 350], [430, 300]],
                   layer="roads", style={"stroke": "#8a7550"})
    w.add_geometry(pass_road.id, "line",
                   [[120, 640], [300, 620], [430, 300]],
                   layer="roads", style={"stroke": "#8a7550", "dash": True})

    # The planner needs statistics before the graph walks are fast. See store/db.py.
    w.analyze()
    return w
