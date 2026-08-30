"""The starting vocabulary of a world.

Every entry here is a *row*, not a class. A new project is seeded with these, and from
then on they are as editable and as deletable as anything the writer invents themselves
(§60). Nothing in the engine special-cases "person" or "settlement"; the succession engine
asks for the `parent_of` predicate by key, and if a writer renames or replaces it, the
configuration follows.

This is what keeps the software out of the trap §60 names: being locked to European
medieval fantasy. The defaults are recognisably that, because most users of a low-fantasy
worldbuilder want them — but they are defaults, not assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EntityTypeDef:
    key: str
    label: str
    plural: str
    category: str
    icon: str = ""
    # §56: what a beginner sees before they expand anything
    core_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class PredicateDef:
    key: str
    label: str
    kind: str                      # 'prop' | 'rel'
    inverse_key: str | None = None
    symmetric: bool = False
    transitive: bool = False
    datatype: str = "text"
    scale_key: str | None = None
    domain_type_keys: tuple[str, ...] = ()
    range_type_keys: tuple[str, ...] = ()
    category: str = "other"
    description: str = ""


@dataclass(frozen=True)
class ScaleDef:
    key: str
    label: str
    steps: tuple[dict, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------- entity types (§2)

ENTITY_TYPES: tuple[EntityTypeDef, ...] = (
    # people and groups
    EntityTypeDef("person", "Person", "People", "people", "person",
                  ("born", "died", "gender", "culture", "religion")),
    EntityTypeDef("house", "Noble house", "Noble houses", "people", "shield",
                  ("founded", "seat", "motto")),
    EntityTypeDef("dynasty", "Dynasty", "Dynasties", "people", "crown"),
    EntityTypeDef("clan", "Clan", "Clans", "people", "people"),
    EntityTypeDef("faction", "Faction", "Factions", "politics", "flag"),
    EntityTypeDef("organization", "Organization", "Organizations", "politics", "building"),
    EntityTypeDef("guild", "Guild", "Guilds", "economy", "hammer"),
    # Groups of people are not only noble houses. These are defaults, not a closed list:
    # §60 means a writer who needs a caste, a lodge or a crew adds one in a click.
    EntityTypeDef("order", "Order", "Orders", "politics", "shield",
                  ("founded", "seat", "vows")),
    EntityTypeDef("tribe", "Tribe", "Tribes", "people", "people"),
    EntityTypeDef("company", "Company", "Companies", "military", "banner",
                  ("founded", "captain", "strength")),
    EntityTypeDef("household", "Household", "Households", "people", "hearth"),
    EntityTypeDef("religion", "Religion", "Religions", "culture", "temple"),
    EntityTypeDef("culture", "Culture", "Cultures", "culture", "mask"),
    EntityTypeDef("language", "Language", "Languages", "culture", "speech"),

    # governance
    EntityTypeDef("realm", "Realm", "Realms", "politics", "crown",
                  ("government", "capital", "ruler")),
    EntityTypeDef("government", "Government", "Governments", "politics", "column"),
    EntityTypeDef("title", "Title", "Titles", "politics", "crown",
                  ("rank", "succession_law")),
    EntityTypeDef("office", "Office", "Offices", "politics", "seal"),
    EntityTypeDef("law", "Law", "Laws", "politics", "scroll"),

    # places
    EntityTypeDef("region", "Region", "Regions", "geography", "map",
                  ("terrain", "climate", "population")),
    EntityTypeDef("settlement", "Settlement", "Settlements", "geography", "town",
                  ("settlement_type", "population", "founded")),
    EntityTypeDef("holding", "Holding", "Holdings", "geography", "manor"),
    EntityTypeDef("site", "Site", "Sites", "geography", "pin"),
    EntityTypeDef("road", "Road", "Roads", "geography", "road"),
    EntityTypeDef("waterway", "Waterway", "Waterways", "geography", "wave"),
    EntityTypeDef("terrain_feature", "Terrain feature", "Terrain features", "geography", "mountain"),

    # economy
    EntityTypeDef("resource", "Resource", "Resources", "economy", "ore"),
    EntityTypeDef("trade_route", "Trade route", "Trade routes", "economy", "caravan"),

    # military
    EntityTypeDef("army", "Army", "Armies", "military", "sword"),
    EntityTypeDef("fleet", "Fleet", "Fleets", "military", "ship"),

    # history and story
    EntityTypeDef("event", "Event", "Events", "history", "spark"),
    EntityTypeDef("war", "War", "Wars", "history", "swords"),
    EntityTypeDef("artifact", "Artifact", "Artifacts", "history", "gem"),
    EntityTypeDef("document", "Document", "Documents", "history", "scroll"),
)


# ---------------------------------------------------------------- scales (§5)
#
# The brief is emphatic that relationships must not reduce to "friend" or "enemy", and that
# the writer must be able to express degree without being forced to invent numbers.

SCALES: tuple[ScaleDef, ...] = (
    ScaleDef("trust", "Trust", (
        {"value": "deeply_trusts", "label": "deeply trusts", "rank": 2},
        {"value": "trusts", "label": "trusts", "rank": 1},
        {"value": "uncertain", "label": "uncertain", "rank": 0},
        {"value": "suspicious", "label": "suspicious of", "rank": -1},
        {"value": "distrusts", "label": "distrusts", "rank": -2},
    )),
    ScaleDef("affection", "Affection", (
        {"value": "loves", "label": "loves", "rank": 2},
        {"value": "fond", "label": "is fond of", "rank": 1},
        {"value": "indifferent", "label": "is indifferent to", "rank": 0},
        {"value": "dislikes", "label": "dislikes", "rank": -1},
        {"value": "hates", "label": "hates", "rank": -2},
    )),
    ScaleDef("intensity", "Intensity", (
        {"value": "overwhelming", "label": "overwhelming", "rank": 3},
        {"value": "strong", "label": "strong", "rank": 2},
        {"value": "moderate", "label": "moderate", "rank": 1},
        {"value": "slight", "label": "slight", "rank": 0},
    )),
    ScaleDef("magnitude", "Magnitude", (
        {"value": "none", "label": "none", "rank": 0},
        {"value": "low", "label": "low", "rank": 1},
        {"value": "medium", "label": "medium", "rank": 2},
        {"value": "high", "label": "high", "rank": 3},
        {"value": "very_high", "label": "very high", "rank": 4},
    )),
)


# ---------------------------------------------------------------- predicates (§2)

def _rel(key, label, inverse=None, **kw) -> PredicateDef:
    return PredicateDef(key=key, label=label, kind="rel", inverse_key=inverse, **kw)


def _prop(key, label, **kw) -> PredicateDef:
    return PredicateDef(key=key, label=label, kind="prop", **kw)


PREDICATES: tuple[PredicateDef, ...] = (
    # -- kinship (§7). Parentage is split by *kind* rather than collapsed, because the
    # difference between a biological and a legal parent is precisely what dynastic
    # fiction turns on.
    _rel("parent_of", "parent of", "child_of", category="kinship",
         domain_type_keys=("person",), range_type_keys=("person",),
         description="Biological parent unless a legitimacy fact says otherwise."),
    _rel("legal_parent_of", "legal parent of", "legal_child_of", category="kinship",
         domain_type_keys=("person",), range_type_keys=("person",)),
    _rel("adoptive_parent_of", "adoptive parent of", "adoptive_child_of", category="kinship",
         domain_type_keys=("person",), range_type_keys=("person",)),
    _rel("foster_parent_of", "foster parent of", "foster_child_of", category="kinship",
         domain_type_keys=("person",), range_type_keys=("person",)),
    _rel("married_to", "married to", symmetric=True, category="kinship",
         domain_type_keys=("person",), range_type_keys=("person",)),
    _rel("betrothed_to", "betrothed to", symmetric=True, category="kinship"),
    _rel("consort_of", "consort of", symmetric=True, category="kinship"),
    _rel("sibling_of", "sibling of", symmetric=True, category="kinship",
         description="Usually derived from shared parents; assert it directly only when "
                     "the parents themselves are unknown."),

    # -- personal feeling (§5). Asymmetric by design: Mara may trust Edric while Edric
    # distrusts Mara, and the model must not average that away.
    _rel("trusts", "trusts", category="feeling", scale_key="trust"),
    _rel("feels_about", "feels about", category="feeling", scale_key="affection"),
    _rel("fears", "fears", category="feeling", scale_key="intensity"),
    _rel("respects", "respects", category="feeling", scale_key="intensity"),
    _rel("resents", "resents", category="feeling", scale_key="intensity"),
    _rel("loyal_to", "loyal to", category="feeling", scale_key="intensity"),
    _rel("rival_of", "rival of", symmetric=True, category="feeling"),
    _rel("owes_debt_to", "owes a debt to", "is_owed_by", category="feeling"),
    _rel("protects", "protects", "protected_by", category="feeling"),

    # -- allegiance and politics (§10). `vassal_of` is transitive: walking it is how
    # "which houses ultimately serve the Crown" is answered.
    _rel("vassal_of", "vassal of", "liege_of", transitive=True, category="politics"),
    _rel("sworn_to", "sworn to", "has_sworn", category="politics"),
    _rel("member_of", "member of", "has_member", category="politics"),
    _rel("head_of", "head of", "headed_by", category="politics"),
    # A minor house is minor because of what it is sworn to, so the hierarchy is a
    # relationship rather than a type. `subgroup_of` generalises it past houses: a
    # chapter of an order, a lodge of a guild, a sept of a clan. Transitive, so one
    # walk answers "everything ultimately under this banner".
    _rel("subgroup_of", "a branch of", "has_branch", transitive=True, category="politics",
         description="A lesser body within a greater one — a cadet house, a chapter, "
                     "a lodge, a sept."),
    _rel("cadet_branch_of", "a cadet branch of", "has_cadet_branch", category="politics",
         description="Descended from a greater house rather than merely sworn to it."),
    _rel("allied_with", "allied with", symmetric=True, category="politics"),
    _rel("at_war_with", "at war with", symmetric=True, category="politics"),
    _rel("claims", "claims", "claimed_by", category="politics"),

    # -- territory (§11). The brief's sharpest distinction: legal ownership, day-to-day
    # administration, military occupation and tax collection are four different facts and
    # may name four different houses at once.
    _rel("legally_owns", "legally owns", "legally_owned_by", category="territory"),
    _rel("administers", "administers", "administered_by", category="territory"),
    _rel("occupies", "militarily occupies", "occupied_by", category="territory"),
    _rel("taxes", "collects taxes from", "taxed_by", category="territory"),
    _rel("rules", "rules", "ruled_by", category="territory"),
    _rel("religious_authority_over", "holds religious authority over",
         "under_religious_authority_of", category="territory"),

    # -- geography (§12)
    _rel("located_in", "located in", "contains", transitive=True, category="geography"),
    _rel("borders", "borders", symmetric=True, category="geography"),
    _rel("connects", "connects", "connected_by", category="geography"),
    _rel("flows_through", "flows through", "has_flowing_through", category="geography"),
    _rel("capital_of", "capital of", "has_capital", category="geography"),
    # Where a *group* belongs. Distinct from located_in, which places a thing inside a
    # place: a guild is seated in one city and active across three regions, and
    # flattening those into one predicate loses the question a writer actually asks.
    _rel("based_in", "based in", "hosts", category="geography",
         description="Where a group is seated — its hall, chapterhouse or stronghold."),
    _rel("active_in", "active in", "has_presence", category="geography",
         description="Where a group operates, which may be far wider than its seat."),
    # A river system is a tree: the Renn is one river with tributaries, not five
    # rivers that happen to touch. Naming that relation is what lets a generated
    # channel take the writer's river name up its own largest branch and stop.
    _rel("flows_into", "flows into", "has_tributary", category="geography",
         description="A watercourse joining a greater one, or reaching the sea."),

    # -- economy (§17, §19)
    # §18's "simple mode" is `grain production: high`, and `magnitude` has spelled
    # none/low/medium/high/very high since the first vocabulary. Without a `scale_key`
    # the fact form hides its strength control entirely, so the seeded world contained
    # `produces … strength="high"` that the application itself could not author.
    _rel("produces", "produces", "produced_by", category="economy",
         scale_key="magnitude"),
    _rel("consumes", "consumes", "consumed_by", category="economy",
         scale_key="magnitude"),
    _rel("imports", "imports", "imported_by", category="economy",
         scale_key="magnitude"),
    _rel("exports", "exports", "exported_by", category="economy",
         scale_key="magnitude"),
    _rel("trades_with", "trades with", symmetric=True, category="economy"),
    # §19 wants a commodity on a route. `carries` was already *read* when the map
    # generator collected a road's goods, and was in no vocabulary and written by
    # nothing — a dead read three layers deep. This is the half that was missing.
    _rel("carries", "carries", "carried_by", category="economy",
         description="§19: what moves along this road, river or trade route."),
    _rel("depends_on", "depends on", "depended_on_by", category="economy",
         description="Systemic dependency, used by failure analysis (§85)."),

    # -- culture (§27, §28, §29)
    _rel("worships", "worships", "worshipped_by", category="culture"),
    _rel("belongs_to_culture", "belongs to the culture", "has_member_culture", category="culture"),
    _rel("speaks", "speaks", "spoken_by", category="culture"),

    # -- military (§23)
    _rel("commands", "commands", "commanded_by", category="military"),
    _rel("garrisoned_at", "garrisoned at", "hosts_garrison", category="military"),

    # -- knowledge and events (§6, §31)
    _rel("knows_about", "knows about", "known_by", category="knowledge"),
    # §93's fog of knowledge, said as a fact rather than a column so it is dated,
    # undoable, branch-aware and askable like everything else. Ignorance is the
    # exception a writer records, not the default the software assumes: a perspective
    # that hid everything not explicitly granted would show an empty map.
    _rel("unaware_of", "has never heard of", "unknown_to", category="knowledge",
         description="§93: what this observer does not know exists. End the fact on "
                     "the day they find out."),
    _rel("participated_in", "participated in", "had_participant", category="history"),
    _rel("witnessed", "witnessed", "was_witnessed_by", category="history"),
    _rel("killed", "killed", "killed_by", category="history"),
    _rel("inherited_from", "inherited from", "bequeathed_to", category="history"),

    # -- properties. Anything a writer might want dated goes through the same spine, which
    # is why 'current location' and 'social rank' are facts rather than entity columns.
    # -- geography, for things a map derives and a writer may correct
    _prop("feature_kind", "Feature kind", category="geography",
          description="What a natural feature is: forest, marsh, downs, waste, ice."),
    _prop("navigable", "Navigable", category="geography",
          description="Whether a watercourse carries traffic, and to what draught."),
    _prop("extent", "Extent", category="geography",
          description="How much ground something covers, in the writer's own words."),
    _prop("area", "Area", category="geography",
          description="Ground covered, in square world units."),
    # Where a generated feature remembers what it was called. Without it a regenerated
    # map renames every town the writer has renamed.
    _prop("map_key", "Map key", category="geography",
          description="The generator's stable name for this feature."),
    _prop("gender", "Gender", category="identity"),
    _prop("alias", "Alias", category="identity"),
    _prop("honorific", "Honorific", category="identity"),
    _prop("occupation", "Occupation", category="identity"),
    _prop("social_rank", "Social rank", category="identity"),
    _prop("located_at", "Located at", category="identity",
          description="Where the subject is at a given time; dated, so a character's "
                      "whereabouts are a history rather than a single field."),
    _prop("legitimacy", "Legitimacy", category="identity",
          description="legitimate | illegitimate | disputed | legitimised"),
    _prop("appearance", "Appearance", category="description"),
    _prop("personality", "Personality", category="psychology"),
    _prop("surface_goal", "Surface goal", category="motivation",
          description="What they openly claim to want (§4)."),
    _prop("private_goal", "Private goal", category="motivation",
          description="What they actually want (§4)."),
    _prop("fundamental_need", "Fundamental need", category="motivation"),
    _prop("fear", "Fear", category="motivation"),
    _prop("pressure_point", "Pressure point", category="motivation"),
    _prop("stakes", "Stakes", category="motivation"),
    _prop("obstacle", "Current obstacle", category="motivation"),
    _prop("leverage", "Leverage", category="motivation"),
    _prop("vulnerability", "Vulnerability", category="motivation"),
    _prop("population", "Population", datatype="number", category="demographics"),
    _prop("settlement_type", "Settlement type", category="geography"),
    _prop("government", "Government type", category="politics"),
    _prop("terrain", "Terrain", category="geography"),
    _prop("climate", "Climate", category="geography"),
    _prop("motto", "Motto", category="identity"),
    _prop("heraldry", "Heraldry", category="identity"),
    _prop("wealth", "Wealth", scale_key="magnitude", category="economy"),
    _prop("prestige", "Prestige", scale_key="magnitude", category="politics"),
    _prop("military_strength", "Military strength", scale_key="magnitude", category="military"),
    _prop("production", "Production level", scale_key="magnitude", category="economy"),
    _prop("strategic_value", "Strategic value", scale_key="magnitude", category="military"),
    _prop("defensibility", "Defensibility", scale_key="magnitude", category="military"),
    _prop("note", "Note", category="other"),
)


PREDICATES_BY_KEY = {p.key: p for p in PREDICATES}
ENTITY_TYPES_BY_KEY = {t.key: t for t in ENTITY_TYPES}
SCALES_BY_KEY = {s.key: s for s in SCALES}


def inverse_of(key: str) -> str | None:
    """The predicate that expresses the same fact from the other side (§77)."""
    p = PREDICATES_BY_KEY.get(key)
    if p is None:
        return None
    if p.symmetric:
        return p.key
    return p.inverse_key


# Confidence and secrecy vocabularies (§57, §6). Kept as plain tuples so the API can
# validate against them and the UI can enumerate them.
CONFIDENCE_LEVELS = ("canon", "draft", "tentative", "rumored", "disputed",
                     "speculative", "unknown", "deprecated")
SECRECY_LEVELS = ("public", "known", "discreet", "secret", "deep_secret")
KNOWLEDGE_STANCES = ("knows", "believes", "suspects", "misinformed", "unaware")
# How much a secret costs when it comes out. Closed so the write surface can validate
# and the client can enumerate, like every other list here.
SECRET_SEVERITIES = ("trivial", "minor", "major", "catastrophic")
