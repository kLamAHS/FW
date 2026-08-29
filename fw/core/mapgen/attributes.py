"""Reading a region's character out of what the writer actually wrote.

The generator needs numbers — how high, how wet, how cold, how big — and the world holds
prose: `terrain = "mountains and forest"`, `climate = "cold, heavy snow in Darkening"`.
Demanding that a writer re-enter their world as tokens before they can see a map would be
the wrong trade, so this module reads tokens *if they exist* and falls back to reading the
prose, and says which it did.

That last part is not decoration. §67 requires derived values to show their work, and the
first question a writer asks about a generated map is "why is my kingdom a desert?" — for
which the honest answer is "because you wrote 'dry' in its climate", or "because you wrote
nothing and everything defaults to temperate".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from fw.core.mapgen import guards
from fw.core.world import World

# Terrain kinds the generator understands, each with the elevation and roughness it
# implies. Elevation is 0 (sea level) to 1 (peaks); roughness drives local relief.
TERRAIN_KINDS: dict[str, tuple[float, float]] = {
    "ocean": (0.02, 0.02),
    "coast": (0.22, 0.06),
    "marsh": (0.19, 0.03),
    "plain": (0.34, 0.05),
    "farmland": (0.32, 0.04),
    "steppe": (0.38, 0.07),
    "desert": (0.40, 0.10),
    "forest": (0.46, 0.12),
    "hills": (0.60, 0.22),
    "highland": (0.68, 0.26),
    "mountain": (0.88, 0.34),
    "glacier": (0.82, 0.20),
}

# The travel engine (§22) knows a coarser set of grounds than the map does, and a route
# segment whose terrain is not one of them is silently unusable: `TransportProfile.speed_on`
# returns 0 for an unknown terrain and the router drops the segment. So a generated road
# has to be described in the router's words, not the map's — "hills" is a landscape, "hill"
# is a travel cost. Every kind above needs an entry here; a test enforces both halves.
ROUTING_TERRAIN: dict[str, str] = {
    "ocean": "water",
    "coast": "plain",
    "marsh": "marsh",
    "plain": "plain",
    "farmland": "plain",
    "steppe": "plain",
    "desert": "desert",
    "forest": "forest",
    "hills": "hill",
    "highland": "hill",
    "mountain": "mountain",
    "glacier": "mountain",
}

# Words a writer plausibly uses, mapped to those kinds. Longest match wins, so
# "mountain pass" reads as mountain rather than nothing.
TERRAIN_WORDS: dict[str, str] = {
    "ocean": "ocean", "sea": "ocean", "gulf": "ocean",
    "coast": "coast", "shore": "coast", "littoral": "coast", "harbour": "coast",
    "harbor": "coast", "estuary": "coast", "delta": "coast",
    "marsh": "marsh", "swamp": "marsh", "fen": "marsh", "bog": "marsh",
    "wetland": "marsh", "moor": "marsh",
    "plain": "plain", "flat": "plain", "lowland": "plain", "vale": "plain",
    "valley": "plain", "meadow": "plain", "grassland": "plain", "prairie": "plain",
    "farmland": "farmland", "farm": "farmland", "arable": "farmland",
    "field": "farmland", "orchard": "farmland", "granary": "farmland",
    "steppe": "steppe", "savanna": "steppe", "scrub": "steppe", "heath": "steppe",
    "desert": "desert", "dune": "desert", "sand": "desert", "badland": "desert",
    "waste": "desert",
    "forest": "forest", "wood": "forest", "taiga": "forest", "jungle": "forest",
    "rainforest": "forest", "timber": "forest",
    "hill": "hills", "downs": "hills", "ridge": "hills", "bluff": "hills",
    "highland": "highland", "plateau": "highland", "upland": "highland",
    "mountain": "mountain", "peak": "mountain", "alpine": "mountain",
    "crag": "mountain", "summit": "mountain", "range": "mountain",
    "glacier": "glacier", "ice": "glacier", "tundra": "glacier",
}

# Climate words, as (temperature, moisture) nudges. Temperature runs -1 (frozen) to
# +1 (scorching); moisture 0 (arid) to 1 (drenched).
CLIMATE_WORDS: dict[str, tuple[float | None, float | None]] = {
    "frozen": (-0.95, None), "arctic": (-0.9, None), "polar": (-0.9, None),
    "snow": (-0.7, 0.6), "ice": (-0.85, None), "cold": (-0.6, None),
    "chill": (-0.45, None), "cool": (-0.3, None), "bleak": (-0.4, 0.4),
    "temperate": (0.0, 0.55), "mild": (0.1, 0.55), "fair": (0.1, 0.5),
    "warm": (0.45, None), "hot": (0.75, None), "scorching": (0.95, 0.1),
    "tropical": (0.8, 0.85), "humid": (0.4, 0.8), "sultry": (0.6, 0.75),
    "monsoon": (0.6, 0.95), "rain": (None, 0.85), "wet": (None, 0.8),
    "damp": (None, 0.7), "fog": (None, 0.7), "mist": (None, 0.65),
    "dry": (None, 0.18), "arid": (None, 0.08), "parched": (None, 0.05),
    "desert": (0.6, 0.05), "windswept": (None, 0.35), "storm": (None, 0.75),
}

DEFAULT_TERRAIN = "plain"


@dataclass
class Trace:
    """One value, and where it came from. §67: derived data shows its work."""

    value: object
    because: str


@dataclass
class RegionProfile:
    """A region reduced to the numbers a generator can build land from."""

    entity_id: str
    name: str
    terrain_mix: dict[str, float] = field(default_factory=dict)
    temperature: float = 0.0          # -1 frozen .. +1 scorching
    moisture: float = 0.5             # 0 arid .. 1 drenched
    population: int = 0
    resources: tuple[str, ...] = ()
    coastal: bool = False
    settlements: tuple[str, ...] = ()     # entity ids located in this region
    traces: dict[str, Trace] = field(default_factory=dict)

    @property
    def base_elevation(self) -> float:
        """The height this region tends toward, from its terrain mix."""
        if not self.terrain_mix:
            return TERRAIN_KINDS[DEFAULT_TERRAIN][0]
        total = sum(self.terrain_mix.values()) or 1.0
        return sum(TERRAIN_KINDS[k][0] * w for k, w in self.terrain_mix.items()) / total

    @property
    def roughness(self) -> float:
        """How broken the ground is — what makes mountains jagged and plains smooth."""
        if not self.terrain_mix:
            return TERRAIN_KINDS[DEFAULT_TERRAIN][1]
        total = sum(self.terrain_mix.values()) or 1.0
        return sum(TERRAIN_KINDS[k][1] * w for k, w in self.terrain_mix.items()) / total

    @property
    def dominant(self) -> str:
        if not self.terrain_mix:
            return DEFAULT_TERRAIN
        return max(sorted(self.terrain_mix), key=lambda k: self.terrain_mix[k])

    def why(self, key: str) -> str:
        trace = self.traces.get(key)
        return trace.because if trace else "left at its default"


def _find_word(text: str, word: str) -> int | None:
    """Where a keyword appears as a WORD, not as a fragment of another.

    Bare substring matching read "a nice year" as arctic (ice), "well drained" as
    drenched (rain) and "orange groves" as mountains (range). A writer's prose has to
    be read the way they wrote it, and a generator that misreads it then explains
    itself with the wrong reason is worse than one that says nothing.

    Plural and adjectival endings still count, so "mountains" and "forested" match.
    """
    match = re.search(rf"\b{re.escape(word)}(?:s|es|ed|y|ish|land|lands)?\b",
                      text or "", flags=re.IGNORECASE)
    return match.start() if match else None


def read_terrain(text: str) -> dict[str, float]:
    """Turn a phrase like 'mountains and forest' into weighted terrain kinds.

    Order of appearance decides weight: a writer who says "forest and some hills" means
    mostly forest. Nothing matched means nothing is claimed, and the caller defaults.
    """
    hits: list[tuple[int, str]] = []
    for word, kind in TERRAIN_WORDS.items():
        position = _find_word(text, word)
        if position is not None:
            hits.append((position, kind))
    if not hits:
        return {}
    # First-named dominates, later ones taper — "mountains and forest" is mountains
    # with forest in it, not an even split, and "hills and forest" is not the same
    # region as "forest and hills".
    weights: dict[str, float] = {}
    for _, kind in sorted(hits):
        weights.setdefault(kind, 1.0 / (len(weights) + 1))
    return weights


def read_climate(text: str) -> tuple[float | None, float | None]:
    """Temperature and moisture from a climate phrase, either possibly unstated."""
    temps: list[float] = []
    wets: list[float] = []
    for word, (temp, wet) in CLIMATE_WORDS.items():
        if _find_word(text, word) is None:
            continue
        if temp is not None:
            temps.append(temp)
        if wet is not None:
            wets.append(wet)
    return (sum(temps) / len(temps) if temps else None,
            sum(wets) / len(wets) if wets else None)


def _number(text: str | None) -> int | None:
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None


def profile_region(world: World, entity_id: str, *, at: int | None = None) -> RegionProfile:
    """Everything the generator needs to know about one region, with provenance."""
    entity = world.get_entity(entity_id)
    if entity is None:
        raise ValueError(f"no entity {entity_id}")

    profile = RegionProfile(entity_id=entity_id, name=entity.name)

    def value_of(key: str) -> str | None:
        # Sorted, because SQLite returns rows in whatever order it finds them and that
        # order shifts as the file is edited — so an unsorted read makes "the latest
        # assertion" mean something different after an unrelated change elsewhere.
        facts = guards.sorted_facts(world.facts_where(key, subject_id=entity_id, at=at))
        return facts[-1].value if facts else None

    # ---- terrain: a token if the writer set one, else their prose, else a default
    token = value_of("terrain_kind")
    prose = value_of("terrain")
    if token and token.lower() in TERRAIN_KINDS:
        profile.terrain_mix = {token.lower(): 1.0}
        profile.traces["terrain"] = Trace(profile.terrain_mix,
                                          f"its terrain is set to “{token}”")
    elif prose and read_terrain(prose):
        profile.terrain_mix = read_terrain(prose)
        named = ", ".join(sorted(profile.terrain_mix))
        profile.traces["terrain"] = Trace(
            profile.terrain_mix, f"“{prose}” reads as {named}")
    else:
        profile.terrain_mix = {DEFAULT_TERRAIN: 1.0}
        profile.traces["terrain"] = Trace(
            profile.terrain_mix,
            "nothing is recorded about its terrain, so it is taken as open country")

    # ---- climate
    temp_token = value_of("temperature")
    climate_prose = value_of("climate")
    prose_temp, prose_wet = read_climate(climate_prose or "")
    explicit_temp = _signed_number(temp_token)
    if explicit_temp is not None:
        profile.temperature = explicit_temp
        profile.traces["temperature"] = Trace(
            explicit_temp, f"its temperature is set to {temp_token}")
    elif prose_temp is not None:
        profile.temperature = prose_temp
        profile.traces["temperature"] = Trace(
            prose_temp, f"“{climate_prose}” reads as "
                        f"{'cold' if prose_temp < -0.2 else 'hot' if prose_temp > 0.2 else 'mild'}")
    else:
        profile.traces["temperature"] = Trace(0.0, "no climate recorded, so temperate")

    wet_token = _signed_number(value_of("rainfall"), lo=0.0, hi=1.0)
    if wet_token is not None:
        profile.moisture = wet_token
        profile.traces["moisture"] = Trace(wet_token, "its rainfall is set directly")
    elif prose_wet is not None:
        profile.moisture = prose_wet
        profile.traces["moisture"] = Trace(prose_wet, f"“{climate_prose}” reads as "
                                                      f"{'wet' if prose_wet > 0.6 else 'dry' if prose_wet < 0.3 else 'moderate'}")
    else:
        profile.traces["moisture"] = Trace(0.5, "no rainfall recorded, so moderate")

    # ---- people
    population = _number(value_of("population"))
    if population:
        profile.population = population
        profile.traces["population"] = Trace(population, f"{population:,} people recorded")
    else:
        profile.traces["population"] = Trace(0, "no population recorded")

    # ---- what it makes. The seed tags resources with `note`, so read both.
    resources: list[str] = []
    for fact in guards.sorted_facts(
            world.facts_where("produces", subject_id=entity_id, at=at)):
        target = world.get_entity(fact.object_id) if fact.object_id else None
        resources.append(target.name if target else (fact.value or ""))
    for fact in guards.sorted_facts(
            world.facts_where("note", subject_id=entity_id, at=at)):
        if fact.value:
            resources.append(fact.value)
    profile.resources = tuple(r for r in dict.fromkeys(resources) if r)
    if profile.resources:
        profile.traces["resources"] = Trace(
            profile.resources, "it produces " + ", ".join(profile.resources))

    # ---- coastal, if the writer said so or the terrain implies it
    coastal_words = {"coast", "ocean"}
    profile.coastal = bool(coastal_words & set(profile.terrain_mix))
    if not profile.coastal and prose:
        profile.coastal = any(w in prose.lower()
                              for w in ("coast", "sea", "port", "harbour", "harbor"))
    if profile.coastal:
        profile.traces["coastal"] = Trace(True, "its description reaches the sea")

    profile.settlements = tuple(
        f.subject_id for f in guards.sorted_facts(
            world.facts_where("located_in", object_id=entity_id, at=at))
        if (e := world.get_entity(f.subject_id)) is not None
        and e.type_key in ("settlement", "holding", "site")
    )
    return profile


def _signed_number(text: str | None, *, lo: float = -1.0,
                   hi: float = 1.0) -> float | None:
    """Parse a number a writer typed, clamped into range. None when unparseable."""
    if text is None:
        return None
    try:
        value = float(str(text).strip())
    except ValueError:
        return None
    return max(lo, min(hi, value))
