"""Reading the writer's sentences for the landforms in them.

Until now the generator read two fields — a region's `terrain` and its `climate` — and
nothing else. Every summary the writer wrote went unread, which means the map has been
ignoring most of what they actually said about their own world. "The only pass over the
Kingsback, and every army that ever took the north came through it" is the single most
load-bearing sentence in a kingdom, and it reached the map as no bits at all.

What comes out is a `Mention`: one landform noun, exactly as the writer typed it, with
everything the sentence around it said. Whether it is a *the only* — which is a hard
constraint, not a hint. Whether it carried a proper name, so "the River Renn" is a thing
with a name rather than the word river. Which other places were named in the same breath,
so a pass can be tied to the range it crosses. And where in the text it sat, because a
writer's first sentence about a place is about the thing that matters most.

Nothing here guesses. A word not in the lexicon claims nothing, which is better than
deciding that "amber" means ore.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class FeatureRole(str, Enum):
    """What kind of thing a landform noun is, which decides what the map can do with it."""

    POINT = "point"        # ford, pass, bridge, crossing, mouth, cape — somewhere exact
    AREA = "area"          # forest, marsh, downs, moor, wold, heath — a stretch of ground
    WATER = "water"        # bay, gulf, sound, strait, sea, lake, mere
    LINEAR = "linear"      # range, spine, ridge, river, road, causeway
    NARROWS = "narrows"    # neck, isthmus, gap, defile, gorge — where a country pinches


# The nouns, and what each one is. Kept deliberately short: this is the vocabulary of
# landforms a low-fantasy map can actually place, and a word that is not here is a word
# the map has no business acting on.
HEADS: dict[str, FeatureRole] = {
    # somewhere exact
    "ford": FeatureRole.POINT, "crossing": FeatureRole.POINT,
    "pass": FeatureRole.POINT, "bridge": FeatureRole.POINT,
    "mouth": FeatureRole.POINT, "cape": FeatureRole.POINT,
    "headland": FeatureRole.POINT, "harbour": FeatureRole.POINT,
    "harbor": FeatureRole.POINT, "anchorage": FeatureRole.POINT,
    "spring": FeatureRole.POINT, "well": FeatureRole.POINT,
    "quarry": FeatureRole.POINT, "mine": FeatureRole.POINT,
    # a stretch of ground
    "forest": FeatureRole.AREA, "wood": FeatureRole.AREA, "weald": FeatureRole.AREA,
    "marsh": FeatureRole.AREA, "fen": FeatureRole.AREA, "bog": FeatureRole.AREA,
    "moor": FeatureRole.AREA, "heath": FeatureRole.AREA, "downs": FeatureRole.AREA,
    "wold": FeatureRole.AREA, "plain": FeatureRole.AREA, "steppe": FeatureRole.AREA,
    "desert": FeatureRole.AREA, "glacier": FeatureRole.AREA, "tundra": FeatureRole.AREA,
    "vale": FeatureRole.AREA, "valley": FeatureRole.AREA, "dale": FeatureRole.AREA,
    "hills": FeatureRole.AREA, "highland": FeatureRole.AREA,
    # water
    "bay": FeatureRole.WATER, "gulf": FeatureRole.WATER, "sound": FeatureRole.WATER,
    "strait": FeatureRole.WATER, "sea": FeatureRole.WATER, "ocean": FeatureRole.WATER,
    "lake": FeatureRole.WATER, "mere": FeatureRole.WATER, "loch": FeatureRole.WATER,
    "estuary": FeatureRole.WATER, "firth": FeatureRole.WATER,
    "delta": FeatureRole.WATER, "lagoon": FeatureRole.WATER,
    # things with a length
    "river": FeatureRole.LINEAR, "brook": FeatureRole.LINEAR,
    "stream": FeatureRole.LINEAR, "beck": FeatureRole.LINEAR,
    "range": FeatureRole.LINEAR, "spine": FeatureRole.LINEAR,
    "ridge": FeatureRole.LINEAR, "escarpment": FeatureRole.LINEAR,
    "road": FeatureRole.LINEAR, "causeway": FeatureRole.LINEAR,
    "coast": FeatureRole.LINEAR, "shore": FeatureRole.LINEAR,
    "mountains": FeatureRole.LINEAR, "peaks": FeatureRole.LINEAR,
    # where a country pinches
    "neck": FeatureRole.NARROWS, "isthmus": FeatureRole.NARROWS,
    "gap": FeatureRole.NARROWS, "defile": FeatureRole.NARROWS,
    "gorge": FeatureRole.NARROWS, "narrows": FeatureRole.NARROWS,
}

# Words that make a mention a constraint rather than a description. "The only pass" is
# not the writer describing scenery; it is them telling the map there is exactly one.
EXCLUSIVE = ("only", "sole", "single", "just one", "no other", "one and only")

# What a sentence says one thing does to another. Small on purpose — a relation the map
# cannot act on is noise, and a wrong one is worse than none.
RELATIONS: dict[str, str] = {
    "guards": "guards", "guarding": "guards", "watches": "guards",
    "overlooks": "guards", "commands": "guards", "commanding": "guards",
    "crosses": "crosses", "spans": "crosses", "carries": "crosses",
    "flows through": "flows_through", "runs through": "flows_through",
    "flows into": "flows_into", "empties into": "flows_into",
    "joins": "flows_into", "meets": "flows_into",
    "rises in": "rises_in", "rises above": "rises_in",
    "separates": "separates", "divides": "separates", "cuts off": "separates",
    "borders": "borders", "marches with": "borders",
    "north of": "north_of", "south of": "south_of",
    "east of": "east_of", "west of": "west_of",
    "above": "above", "below": "below", "between": "between",
}

# The words a proper name is dressed in, so "the River Renn" yields "The River Renn" and
# "a river" yields nothing.
_ARTICLES = ("the", "a", "an")


@dataclass(frozen=True)
class Mention:
    """One landform the writer named, with everything its sentence said about it."""

    head: str                       # the canonical noun: "pass", "bay", "range"
    role: FeatureRole
    surface: str                    # exactly what they typed
    proper_name: str | None         # "The River Renn"; None if they were being generic
    record_key: str                 # "region/the-northmarch#summary"
    sentence: str
    position: int                   # character offset, so first-mentioned reads as first
    exclusive: bool = False         # "the ONLY pass" — a constraint, not a hint
    anchors: tuple[str, ...] = ()   # other names in the same sentence
    relation: str | None = None


_SENTENCE = re.compile(r"[^.!?;]+[.!?;]?")
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")


def sentences(text: str) -> list[tuple[int, str]]:
    """The text cut into sentences, each with where it started."""
    out: list[tuple[int, str]] = []
    for match in _SENTENCE.finditer(text or ""):
        piece = match.group().strip()
        if piece:
            out.append((match.start(), piece))
    return out


def scan(text: str, record_key: str, *,
         gazetteer: frozenset[str] = frozenset()) -> tuple[Mention, ...]:
    """Every landform the writer mentioned in one piece of prose.

    `gazetteer` is the set of names already in their world, normalised, so a sentence
    that says "the pass above Northwatch" ties the pass to Northwatch rather than to
    nothing. It is a set of strings and is only ever *tested* against, never iterated —
    iterating a set of strings is how a generated map stops being reproducible.
    """
    found: list[Mention] = []
    for offset, sentence in sentences(text or ""):
        lower = sentence.lower()
        exclusive = any(word in lower for word in EXCLUSIVE)
        relation = _relation_in(lower)
        anchors = _anchors_in(sentence, gazetteer)
        for word in _WORD.finditer(sentence):
            head = _canonical(word.group())
            if head is None:
                continue
            found.append(Mention(
                head=head, role=HEADS[head], surface=word.group(),
                proper_name=_proper_name(sentence, word.start(), word.end()),
                record_key=record_key, sentence=sentence,
                position=offset + word.start(), exclusive=exclusive,
                anchors=anchors, relation=relation))
    # Sorted by where they appear, which is the only order that means anything about
    # the writer's intent, with the noun as a stable tie-break.
    found.sort(key=lambda m: (m.position, m.head))
    return tuple(found)


def _canonical(word: str) -> str | None:
    """The lexicon entry a word belongs to, allowing the endings English adds."""
    plain = word.lower()
    if plain in HEADS:
        return plain
    for ending in ("es", "s"):
        if plain.endswith(ending) and plain[: -len(ending)] in HEADS:
            return plain[: -len(ending)]
    return None


def _proper_name(sentence: str, start: int, end: int) -> str | None:
    """The name a landform was given, if it was given one.

    Two shapes, both of which a writer uses without thinking: the noun follows the name
    ("the Kingsback Range", "Redwater Ford") or precedes it ("the River Renn", "the Bay
    of Storms"). A lower-case noun on its own is generic and names nothing.

    The capital that begins a sentence is not evidence of anything. Without that rule
    "Sheltered anchorage at the mouth of the Renn" yields a harbour called Sheltered.
    """
    noun = sentence[start:end]
    before = sentence[:start].rstrip()
    after = sentence[end:].lstrip()

    # A capital that opens a sentence is grammar, not a name — unless the noun after it
    # is capitalised too, which is the writer signalling one. "Redwater Ford is the only
    # crossing" names a ford; "Sheltered anchorage at the mouth" does not name anything.
    words = list(_WORD.finditer(before))
    lead = [w for w in words[-2:]
            if w.group()[:1].isupper() and w.group().lower() not in _ARTICLES
            and (noun[:1].isupper()
                 or not _opens_the_sentence(sentence, w.start()))]
    if lead:
        # The article sits before the *name*, not before the noun: in "the Kingsback
        # Range" the "the" is two words back.
        return (_article(sentence[:lead[0].start()])
                + " ".join([*(w.group() for w in lead), noun]))

    following = list(_WORD.finditer(after))[:2]
    trail = [w.group() for w in following if w.group()[:1].isupper()]
    if trail:
        # "the Bay of Storms" — the joining word is part of the name, not a gap in it.
        joiner = "of " if after.lower().startswith("of ") else ""
        if noun[:1].isupper() or joiner:
            return _article(before) + " ".join([noun, *(joiner.split() + trail)])
    return None


def _opens_the_sentence(sentence: str, at: int) -> bool:
    """Is this the first word? Then its capital is grammar, not a name."""
    return not sentence[:at].strip()


def _article(before: str) -> str:
    """A name the writer wrote with "the" keeps it: the River Renn, not River Renn."""
    words = _WORD.findall(before)
    return "The " if words and words[-1].lower() == "the" else ""


def _relation_in(lower: str) -> str | None:
    for phrase in sorted(RELATIONS, key=lambda p: (-len(p), p)):
        if phrase in lower:
            return RELATIONS[phrase]
    return None


def _anchors_in(sentence: str, gazetteer: frozenset[str]) -> tuple[str, ...]:
    """Names from the writer's own world that this sentence also mentions.

    Longest first, so "The Vale of Renn" is found rather than "Renn" inside it, and the
    result is a sorted tuple because anything that reaches a seed or a key has to be in
    a stable order.
    """
    if not gazetteer:
        return ()
    lower = sentence.lower()
    hit: set[str] = set()
    for name in gazetteer:
        if name and name in lower:
            hit.add(name)
    # Drop any name wholly contained in a longer one that also matched.
    kept = [n for n in hit if not any(n != other and n in other for other in hit)]
    return tuple(sorted(kept))
