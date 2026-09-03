"""The continent itself: where the rain falls, where the rivers run, who is where.

Built in the order a geographer would build it, because on this continent the geography
causes the history. Wet westerlies come off the Western Sea, water Merran and the Carth
Basin, rise over the Eastern Mountains and fall dry on the far side — which is why the
Orri keep herds instead of fields. The Carth rises in those mountains, crosses the
basin, and reaches the sea through Merran, which is why Merran is rich without growing
anything much, and why every strong Nyren king eventually decides he must have the
river's mouth.

Every shape here is authored — drawn before any terrain exists, so it cannot follow
generated ground. It has only to be plausible, and to sit inside the rim: the map is
900 units across with a ramp (`coast.MARGIN`) that drowns anything past |d| ≈ 0.85 from
the centre, so every vertex stays inside roughly 130…770. The old example world learned
that the hard way — a province drawn out to x=40 forced the raster's edge column dry
and the picture was guillotined flat down one side.
"""

from __future__ import annotations

# The continent's outline, west of which is the Western Sea, north of which is the
# Northern Sea and Nyreland beyond it. Drawn as a coast is drawn — headlands, bays, a
# southwestern peninsula and the Carth's estuary — rather than as a rectangle with
# noise on it. The generator decides the true waterline; this says where the land is.
NORTH_COAST = [
    (214, 330), (247, 313), (283, 306), (318, 314), (349, 301), (384, 308),
    (417, 298), (452, 306), (487, 297), (521, 308), (553, 299), (586, 312),
    (617, 304), (648, 318),
]
EAST_COAST = [
    (679, 336), (706, 362), (721, 395), (715, 430), (731, 462), (742, 498),
    (736, 535), (745, 571), (731, 606),
]
SOUTH_COAST = [
    (707, 635), (676, 657), (642, 672), (607, 663), (573, 676), (539, 668),
    (505, 681), (470, 673), (436, 686), (401, 678), (367, 690), (333, 681),
    (300, 693), (268, 684),
]
WEST_COAST = [
    (240, 666), (219, 641), (228, 612), (211, 585), (224, 556),
    # The Carth's estuary: a wedge of sea driven inland, and the reason Orra sits
    # where it does. A river mouth is a notch in a coast, not a dot on it.
    (203, 538), (228, 521), (206, 499),
    (219, 472), (198, 452), (211, 424), (190, 398), (203, 371), (186, 347),
]

COAST = NORTH_COAST + EAST_COAST + SOUTH_COAST + WEST_COAST

# The interior divisions, each drawn once and used by both regions either side, so the
# eight rings tile the continent instead of nearly agreeing. Named for the two they
# separate, and running in one stated direction; a region that needs the other takes it
# reversed. §66: none of this is a survey — it is where the writer says these countries
# are, and the generator traces the territory each claim implies.

# Vardi's south-eastern limit: the uplands falling away toward the basin.
VARDI_INLAND = [(283, 306), (298, 331), (289, 357), (303, 382), (294, 407),
                (277, 419), (258, 428)]

# Merran's inland edge, north to south: the line beyond which the coast stops paying.
MERRAN_INLAND = [
    (258, 428), (281, 452), (297, 481), (288, 511), (303, 540), (292, 569),
    (307, 598), (291, 627), (299, 656), (272, 673),
]

# Where the northern forest gives way to ploughed basin, west to east.
SELLI_CARTH = [
    (285, 414), (305, 396), (332, 380), (366, 390), (400, 377), (434, 387),
    (468, 374), (502, 384), (536, 371), (570, 381), (604, 366), (639, 349),
]

# Where the basin rises into the southern hills, west to east.
CARTH_TALARI = [
    (299, 656), (334, 638), (369, 650), (404, 639), (439, 652), (474, 641),
    (509, 654), (544, 643), (578, 655), (607, 637),
]

# The mountains' western wall, north to south: the Carth's headwaters are behind it.
CARTH_MOUNTAINS = [
    (639, 349), (614, 379), (623, 411), (609, 442), (620, 474), (606, 505),
    (617, 537), (603, 568), (614, 600), (607, 637),
]

# Their eastern foot, where the rain has already fallen and the grass turns short.
MOUNTAINS_ORRI = [
    (648, 318), (677, 350), (666, 383), (679, 416), (668, 449), (681, 482),
    (670, 515), (683, 548), (672, 581), (685, 614), (642, 672),
]


def _arc(first, last):
    """A run of the coast, by the index of its first and last vertex."""
    return COAST[first:last + 1]


def _ring(*parts):
    """One region's outline. Seam vertices two rings share are kept exactly once."""
    out: list[tuple[float, float]] = []
    for part in parts:
        for point in part:
            if not out or point != out[-1]:
                out.append(point)
    while len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return [[float(x), float(y)] for x, y in out]


def _back(line, *, drop_first=0, drop_last=0):
    """A shared boundary walked the other way, which is how the far side needs it."""
    walk = list(reversed(line))
    return walk[drop_first:len(walk) - drop_last]


REGIONS = {
    # Rugged, wet, poor country for wheat and excellent country for sheep, iron and
    # the timber Merran's shipyards cannot do without.
    "The Vardi Uplands": _ring(_arc(47, 50), _arc(0, 2), VARDI_INLAND),
    # Cold forest, lakes and amber. The Nyri came ashore here, which is the only
    # reason anyone south of it has ever had to learn its name.
    "The Selli North": _ring(_arc(2, 13), [(639, 349)], _back(SELLI_CARTH),
                             _back(VARDI_INLAND, drop_first=2, drop_last=1)),
    # The continent's granary, and the reason there is anything worth conquering.
    "The Carth Basin": _ring(SELLI_CARTH, CARTH_MOUNTAINS, _back(CARTH_TALARI),
                             _back(MERRAN_INLAND, drop_first=1, drop_last=1)),
    # Warm limestone hills: wine, oil and fruit the basin cannot grow.
    "The Talari South": _ring(CARTH_TALARI, _arc(25, 36), [(272, 673)]),
    # Ports, not farmland. The Carth reaches the sea here, and that is the whole of
    # Merran's fortune and most of its danger.
    "The Merran Coast": _ring(_arc(36, 47), MERRAN_INLAND),
    # Iron, silver, and the sources of the Carth, which the basin holds sacred.
    "The Eastern Mountains": _ring(CARTH_MOUNTAINS, _back(MOUNTAINS_ORRI)),
    # Beyond the rain shadow: short grass, long winters, and the best horses anyone
    # on this continent has ever ridden.
    "The Orri Steppe": _ring(_arc(13, 25), _back(MOUNTAINS_ORRI, drop_first=1,
                                                 drop_last=1)),
}


# Where the towns are. Chosen from what the ground offers rather than spread evenly:
# Merran's five cities string along the one coast worth having, the basin's three sit
# on the river that feeds them, and the Orri have a horse fair rather than a capital.
PLACES = {
    # Merran, north to south. Orra stands in the Carth's estuary, which is the whole
    # argument of the last four hundred years in one map reference.
    "Calven": (238, 462), "Orra": (216, 527), "Veyra": (255, 571),
    "Meret": (240, 617), "Sere": (271, 665),
    # The Carth Basin, downstream to up.
    "Belcar": (344, 519), "Threeforks": (446, 522), "Orath": (571, 471),
    # Nyren's own foundations: the beach they landed on and the capital they built
    # once the court stopped pretending it would go home. Nyrholt, across the Northern
    # Sea, has no coordinate here: this is a map OF the continent, and the homeland is
    # off the top of it. See the note on the sea crossing in `story`.
    "Nyrmark": (503, 336), "Hadrin": (409, 421),
    # The peoples who were already here.
    "Ambermere": (350, 349), "Kel Varro": (243, 379), "Talvere": (430, 668),
    "Sarth": (617, 505), "Orrek": (702, 452),
}

# The Carth: born in the Eastern Mountains above Sarth, across the whole basin, and
# into the Western Sea through Merran. Every mile of it is somebody's argument.
CARTH = [
    (614, 494), (592, 486), (573, 503), (556, 521), (534, 514), (512, 497),
    (489, 491), (466, 504), (446, 522), (424, 529), (402, 518), (381, 503),
    (359, 499), (338, 510), (316, 527), (295, 536), (274, 528), (253, 514),
    (232, 517), (212, 526),
]
# The two that give Threeforks its name, off the northern forest and the southern hills.
NORTH_FORK = [
    (461, 387), (449, 404), (456, 424), (441, 441), (452, 459), (438, 476),
    (451, 493), (441, 509), (446, 522),
]
SOUTH_FORK = [
    (492, 631), (476, 616), (483, 597), (467, 583), (473, 564), (458, 551),
    (462, 536), (446, 522),
]

# The roads. Nyri royal engineering runs north to south, because that is the way the
# conquest came; Merran's runs along its own coast, because that is where its cities
# are and it has never wanted to go inland.
ROADS = {
    "The King's Road": [
        (503, 336), (481, 356), (462, 377), (436, 391), (409, 421), (421, 448),
        (409, 474), (424, 497), (446, 522), (438, 549), (449, 575), (436, 601),
        (444, 628), (430, 668),
    ],
    "The Coast Road": [
        (238, 462), (253, 484), (245, 507), (216, 527), (245, 545), (255, 571),
        (243, 594), (240, 617), (256, 640), (271, 665),
    ],
    "The Timber Road": [
        (243, 379), (259, 399), (250, 421), (262, 442), (238, 462),
    ],
}


# What each country is made of. The generator reads `terrain` and `climate` off a
# region and grows ground from them (`attributes.profiles_from`), so these are not
# scene-setting — they are the instructions the map is built from. The rain shadow is
# the point: wet westerlies water Merran and the basin, rise over the mountains, and
# arrive on the far side with nothing left to give.
GROUND = {
    "The Merran Coast": ("coast, estuary and green hills", "mild and wet, westerly all year",
               "1 240 000"),
    "The Vardi Uplands": ("hills, crags and deep forest", "cool and very wet",
                          "410 000"),
    "The Selli North": ("pine forest, lakes and marsh", "cold winters, short summers",
                        "560 000"),
    "The Carth Basin": ("river plain and floodmeadow", "temperate, spring floods",
                        "3 900 000"),
    "The Talari South": ("limestone hills and warm valleys", "warm and dry in summer",
                         "870 000"),
    "The Eastern Mountains": ("high mountains and cut valleys", "alpine, snow-fed",
                              "180 000"),
    "The Orri Steppe": ("open steppe and short grass", "dry, cold winters, hot summers",
                        "290 000"),
}

# Who borders whom, which is what makes a frontier a frontier rather than an edge.
BORDERS = [
    ("The Vardi Uplands", "The Selli North"), ("The Vardi Uplands", "The Merran Coast"),
    ("The Vardi Uplands", "The Carth Basin"), ("The Selli North", "The Carth Basin"),
    ("The Carth Basin", "The Merran Coast"), ("The Carth Basin", "The Talari South"),
    ("The Carth Basin", "The Eastern Mountains"), ("The Merran Coast", "The Talari South"),
    ("The Talari South", "The Eastern Mountains"),
    ("The Eastern Mountains", "The Orri Steppe"),
]


def build(w) -> dict:
    """The regions, the waters and the towns, as entities the rest can hang facts on."""
    d = w.day
    made: dict[str, object] = {}

    for name, (terrain, climate, people) in GROUND.items():
        region = w.add_entity("region", name)
        made[name] = region
        w.assert_fact(region, "terrain", value=terrain)
        w.assert_fact(region, "climate", value=climate)
        w.assert_fact(region, "population", value=people.replace(" ", ""))
    for near_side, far_side in BORDERS:
        w.assert_fact(made[near_side], "borders", made[far_side])

    # Defensibility is not decoration: the mountains and the uplands are why two
    # peoples still exist and one of them still has a king.
    w.assert_fact(made["The Eastern Mountains"], "defensibility", value="very high",
                  strength="very_high")
    w.assert_fact(made["The Vardi Uplands"], "defensibility", value="high",
                  strength="high")
    w.assert_fact(made["The Merran Coast"], "defensibility", value="high", strength="high",
                  note="Walled cities a fleet can supply. Land armies have learnt this "
                       "four times.")

    towns = {
        # name: (region, rank, people, founded, summary)
        "Meret": ("The Merran Coast", "city", "84000", 210,
                  "The richest city on the continent, and the one that lends to kings."),
        "Orra": ("The Merran Coast", "port", "51000", 168,
                 "In the Carth's mouth. Whoever holds Orra decides what the basin's "
                 "harvest is worth."),
        "Calven": ("The Merran Coast", "port", "37000", 231,
                   "Shipyards, and the Vardi timber that feeds them."),
        "Veyra": ("The Merran Coast", "fortress", "22000", 640,
                  "Raised in the Conquest to hold the coast road, and never taken."),
        "Sere": ("The Merran Coast", "port", "19000", 302,
                 "Where Talari wine leaves the continent."),
        "Hadrin": ("The Carth Basin", "capital", "96000", 631,
                   "Nyren's capital: a Nyri fortress that became a court, then a city."),
        "Threeforks": ("The Carth Basin", "city", "44000", 96,
                       "Where the Carth takes in both its forks. Carthi long before it "
                       "was anything else."),
        "Belcar": ("The Carth Basin", "market town", "12000", 143,
                   "Grain going downriver stops here to be counted and taxed."),
        "Orath": ("The Eastern Mountains", "capital", "16000", 61,
                  "Carthain's capital in the upper valley, and the oldest crown on the "
                  "continent."),
        "Nyrmark": ("The Selli North", "port", "26000", 612,
                    "The beach the Nyri landed on, and the first thing they built."),
        "Ambermere": ("The Selli North", "town", "7400", 88,
                      "Amber, pitch and furs, sold to whoever comes north for them."),
        "Kel Varro": ("The Vardi Uplands", "town", "5600", 74,
                      "Clan Varro's seat above the timber valleys."),
        "Talvere": ("The Talari South", "market town", "13000", 122,
                    "Wine, oil and a market older than any law that governs it."),
        "Sarth": ("The Eastern Mountains", "town", "3100", 40,
                  "Above the Carth's springs. The basin has walked here for a "
                  "thousand years."),
        "Orrek": ("The Orri Steppe", "town", "4200", 180,
                  "Not a city — a horse fair that never quite ends."),
        }
    for name, (region, rank, people, founded, summary) in towns.items():
        place = w.add_entity("settlement", name, summary=summary,
                             exists_from=d(founded))
        made[name] = place
        w.assert_fact(place, "located_in", made[region])
        w.assert_fact(place, "settlement_type", value=rank)
        w.assert_fact(place, "population", value=people)

    # ------------------------------------------------------------------ water
    carth = w.add_entity(
        "waterway", "The Carth",
        summary="Rises above Sarth, crosses the basin, and reaches the sea at Orra. "
                "The continent's road, its border, and its argument.")
    made["The Carth"] = carth
    for place in ("Threeforks", "Belcar", "Orra"):
        w.assert_fact(carth, "flows_through", made[place])
    w.assert_fact(carth, "navigable", value="from Orra to Threeforks in any season")
    for name in ("The North Fork", "The South Fork"):
        fork = w.add_entity("waterway", name)
        made[name] = fork
        w.assert_fact(fork, "flows_into", carth)
        w.assert_fact(fork, "flows_through", made["Threeforks"])

    # Nyreland is a place in this world and not a country on this map. The coast model
    # shapes ONE continent — `_build_landmass` says so in its first line — so a second
    # landmass across a strait is outside what it does: given one, it filled the gap and
    # the Northern Sea stopped existing, which is the one piece of water the whole
    # history depends on. A map of the continent has the homeland off the top of it,
    # which is what "across the sea" means. Everything else about it is still here.
    made["Nyreland"] = w.add_entity(
        "terrain_feature", "Nyreland",
        summary="Across the Northern Sea: cereal plain, forest and mine, and for two "
                "hundred years now a province of its own colony.")
    w.assert_fact(made["Nyreland"], "feature_kind", value="land beyond the map")
    w.assert_fact(made["Nyreland"], "climate", value="cold and clear")
    w.assert_fact(made["Nyreland"], "population", value="2100000")
    nyrholt = w.add_entity(
        "settlement", "Nyrholt", exists_from=d(402),
        summary="Nyreland's capital, and no longer the capital of anything else. Off "
                "the northern edge of this map, ten days' sailing from Nyrmark.")
    made["Nyrholt"] = nyrholt
    w.assert_fact(nyrholt, "located_in", made["Nyreland"])
    w.assert_fact(nyrholt, "settlement_type", value="city")
    w.assert_fact(nyrholt, "population", value="58000")

    seas = {
        "The Western Sea": "Wide, cold, and the reason Merran faces the way it does.",
        "The Northern Sea": "Narrow enough to cross, which decided everything.",
        "The Southern Sea": "Warm, shallow, and full of Talari wine going somewhere.",
    }
    for name, summary in seas.items():
        made[name] = w.add_entity("terrain_feature", name, summary=summary)
        w.assert_fact(made[name], "feature_kind", value="sea")

    # ------------------------------------------------------------------ roads
    for name, summary, founded in (
        ("The King's Road", "Nyrmark to Talvere by way of Hadrin. Nyri engineering, "
                            "and the spine the conquest was administered down.", 634),
        ("The Coast Road", "Calven to Sere along the Merran shore. Veyra sits on it "
                           "because of it.", 288),
        ("The Timber Road", "Kel Varro down to Calven's yards, loaded one way.", 341),
    ):
        road = w.add_entity("road", name, summary=summary, exists_from=d(founded))
        made[name] = road
    for road, ends in (("The King's Road", ("Nyrmark", "Talvere")),
                       ("The Coast Road", ("Calven", "Sere")),
                       ("The Timber Road", ("Kel Varro", "Calven"))):
        for end in ends:
            w.assert_fact(made[road], "connects", made[end])
    return made


def draw(w, made: dict) -> None:
    """The writer's own shapes (§66), drawn last so everything they name exists."""
    for name, ring in REGIONS.items():
        w.add_geometry(made[name].id, "polygon", [ring], layer="regions")
    for name, points in (("The Carth", CARTH), ("The North Fork", NORTH_FORK),
                         ("The South Fork", SOUTH_FORK)):
        w.add_geometry(made[name].id, "line", [list(p) for p in points],
                       layer="waterways")
    for name, points in ROADS.items():
        w.add_geometry(made[name].id, "line", [list(p) for p in points], layer="roads")
    for name, point in PLACES.items():
        w.add_geometry(made[name].id, "point", list(point), layer="settlements")
