"""Naming generated places in the writer's own voice (§67, §115).

A generated town called "Unnamed settlement 3 (The Sunlit Coast)" is not a place. But a
town called Ysolwyn in a world of Greyhaven and Rennford is barely better — it is a name
out of a different book. So this learns from the names the writer has *already* chosen,
and builds in their register.

The load-bearing observation is that place names in this register are **compounds**:
Grey|haven, Black|mere, Mill|brook, North|watch, Renn|ford, North|march, Salt|Reach.
Split the writer's own names at their endings and you have two vocabularies that belong
to this world and no other — its prefixes and its endings — and recombining them gives
Saltford, Blackhaven, Millwatch: names the writer could have written and didn't.

Which ending is not arbitrary either. Real toponyms say what the ground is: a town at a
crossing is a *ford*, at a sheltered anchorage a *haven*, under a watchpost a *watch*.
The generator already knows why it put a town where it did, so the name carries that
reason — which is how Red Ford got its name, and why a reader can look at a map and
learn something from it.

A world whose names do not compound (Ysolde, Aubrienne, Kethiel) gets a character n-gram
model over the same corpus instead. Either way the names come from the writer's world.

Everything here is a pure function of that corpus and a seed. No RNG, no clocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from fw.core.mapgen import noise

ORDER = 2                      # characters of context in the fallback chain
MIN_CORPUS = 5                 # below this a kind borrows the whole world's place names
MIN_COMPOUND = 0.4             # share of names that must split before compounding wins
MAX_LENGTH = 14
BOUNDARY = "^"
END = "$"

# The endings a name falls back on when the world's own vocabulary is exhausted. Plain,
# common, and letters rather than digits — see `_last_resort`.
_LAST_DITCH = ("stead", "ford", "holt", "wick", "combe", "thorpe", "mere", "hollow",
               "reach", "gate", "hill", "vale", "moor", "bank", "field", "close")

# And how a real place name is told from its neighbour once every stem and ending in the
# world has been spent. Not by counting — Greyhaven2 is not a place, it is a variable —
# but the way English toponymy has always done it, which is why there is a Great Missenden
# and a Little Missenden rather than a Missenden 1 and a Missenden 2.
_QUALIFIERS = ("Upper", "Lower", "Little", "Great", "Old", "New",
               "North", "South", "East", "West", "Nether", "Over")

# Toponymic endings and the ground each one claims. This is the register most English
# language worlds are written in, so it is a reasonable prior for *reading* the writer's
# names. It is never imposed: an ending the writer's own world does not use is only
# offered when their world offers nothing suitable itself.
MORPHEMES: dict[str, tuple[str, ...]] = {
    # settlements, by what the site is
    "ford": ("ford", "crossing"), "bridge": ("ford", "crossing"),
    "haven": ("harbour", "mouth"), "port": ("harbour", "mouth"),
    "mouth": ("mouth", "estuary"), "wick": ("harbour", "coast"),
    "mere": ("lake", "marsh"), "marsh": ("marsh",), "fen": ("marsh",),
    "moor": ("marsh", "upland"), "brook": ("stream",), "beck": ("stream",),
    "watch": ("defence", "height"), "keep": ("defence",), "hold": ("defence",),
    "guard": ("defence",), "gate": ("pass", "chokepoint"), "pass": ("pass",),
    "hill": ("height",), "bury": ("height", "defence"), "tor": ("height",),
    "crag": ("height",), "dale": ("valley",), "vale": ("valley",),
    "hollow": ("valley",), "combe": ("valley",), "wood": ("forest",),
    "holt": ("forest",), "shaw": ("forest",), "field": ("arable",),
    "ley": ("arable",), "croft": ("arable",), "garth": ("arable",),
    "cross": ("junction",), "market": ("junction",), "stead": ("arable", "farm"),
    "delve": ("ore",), "forge": ("ore",), "mine": ("ore",),
    # regions and country
    "march": ("region", "frontier"), "reach": ("region",), "wold": ("region", "upland"),
    "land": ("region",), "cape": ("coast", "region"), "coast": ("coast",),
    "shire": ("region",), "mark": ("region", "frontier"),
    "weald": ("region", "forest"), "downs": ("region", "upland"),
    "fells": ("region", "upland"), "wilds": ("region",), "waste": ("region", "arid"),
    # water
    "water": ("river",), "run": ("river",), "flow": ("river",), "rush": ("river",),
}

# Which entity types feed which kind of name, most apt first. A castle should sound like
# the world's other strongholds, and a river must never be named after a person — the
# pooled fallback is places only, which is the whole point of listing them.
CORPORA: dict[str, tuple[str, ...]] = {
    "settlement": ("settlement", "holding", "site"),
    "castle": ("holding", "settlement", "site"),
    "waterway": ("waterway", "site"),
    "region": ("region", "realm"),
    "site": ("site", "settlement"),
    "road": ("road",),
}
PLACE_TYPES = ("region", "realm", "settlement", "holding", "site", "waterway", "road")

# Words that are the *head* of a name rather than what distinguishes it. "Kingdom of
# Renn" and "River Renn" are both a generic plus a proper noun, and the proper noun is
# the part that belongs to this world — read them the other way round and a generated
# region comes out called Kingdom March.
GENERIC_HEADS = frozenset({
    "kingdom", "realm", "empire", "duchy", "county", "barony", "principality",
    "river", "lake", "loch", "mount", "mountain", "isle", "island", "bay", "gulf",
    "sea", "sound", "strait", "cape", "point", "city", "town", "village", "port",
    "fort", "castle", "keep", "hall", "tower", "abbey", "house", "clan", "order",
    "temple", "shrine", "wood", "forest", "hills", "downs", "plains",
    "the", "and",
})
# Deliberately NOT generic: north, old, great, upper. They lead plenty of real names
# ("North Renn") but they are also this register's most productive prefixes, and
# Northford and Oldgate are worth more than the handful of names they misread.

# Words a name may trail that say what kind of thing it is, not where it is.
TRAILING_GENERICS = frozenset({"road", "way", "path", "trail", "track", "route"})

# Compounds this register throws up that already mean something else in English. A map
# with a town called Blackmarket on it is a map the writer has to go and fix.
AVOID = frozenset({
    "blackmarket", "blackwatch", "redcross", "whitehouse", "greymarket",
    "graymarket", "blacklist", "redlight", "greenhouse", "blackwater",
})

_ARTICLE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)
_JUNK = re.compile(r"[^a-z' ]+")
_OF = re.compile(r"\s+of\s+", re.IGNORECASE)


def _normalise(name: str) -> tuple[str, bool]:
    """A name reduced to what the model learns from, and whether it wore an article."""
    stripped = _ARTICLE.sub("", name.strip())
    had_article = stripped != name.strip()
    return _JUNK.sub("", stripped.lower()).strip(), had_article


def _split(name: str) -> tuple[str, str] | None:
    """Break a compound into (prefix, ending), longest ending first.

    "greyhaven" -> ("grey", "haven"); "red ford" -> ("red", "ford").

    Plurals count. Half the names in this register are plural — Goldhills, Riverlands,
    Barrowdowns — and a reader that only knows the singular finds no compound in them
    at all, decides the world does not compound, and falls back to spelling names out a
    letter at a time. That produced "The Ashwashwast".
    """
    for ending in sorted(MORPHEMES, key=len, reverse=True):
        for form in (ending, ending + "s"):
            if name.endswith(form) and len(name) - len(form) >= 3:
                return name[: -len(form)].strip(), ending
    return None


def _join(prefix: str, ending: str) -> str:
    """Glue two morphemes, eliding a doubled letter at the seam.

    "north" + "haven" is Northaven, not Northhaven — which is how English does it and
    how the writer's own names already read.
    """
    if prefix and ending and prefix[-1] == ending[0]:
        return prefix + ending[1:]
    return prefix + ending


def _usable_prefix(word: str) -> bool:
    """A word that distinguishes a place, rather than saying what kind it is.

    Length matters: Northwatch is a fine name and a terrible prefix, because
    Northwatchmarch is not a word anyone would write.
    """
    return (3 <= len(word) <= 8 and " " not in word
            and word not in MORPHEMES and word not in GENERIC_HEADS)


@dataclass
class _Parsed:
    """One of the writer's names, taken apart."""

    prefixes: tuple[str, ...] = ()
    ending: str = ""
    of_head: str = ""            # the generic in "Vale of Renn"
    of_tail: str = ""            # the proper noun in "Vale of Renn"
    compound: bool = False


def _parse(name: str) -> _Parsed:
    """Read one name for the parts of it that belong to this world.

    Four shapes, in order: a trailing generic ("The Iron Road"), an "X of Y"
    ("The Vale of Renn"), a leading generic ("The River Renn"), and a compound
    ("Greyhaven", "Red Ford"). Anything else is a bare proper noun.
    """
    words = name.split()
    if len(words) > 1 and words[-1] in TRAILING_GENERICS:
        return _parse(" ".join(words[:-1]))

    parts = _OF.split(name)
    if len(parts) == 2:
        head, tail = parts[0].strip(), parts[1].strip()
        piece = _split(head)
        return _Parsed(
            prefixes=(tail,) if _usable_prefix(tail) else (),
            # "Vale of Renn" teaches the world's word for a valley; "Kingdom of Renn"
            # teaches nothing about ground, so only a real morpheme becomes an ending.
            ending=piece[1] if piece else (head if head in MORPHEMES else ""),
            of_head=head, of_tail=tail,
        )

    if len(words) > 1 and words[0] in GENERIC_HEADS:
        tail = " ".join(words[1:])
        return _Parsed(prefixes=(tail,) if _usable_prefix(tail) else ())

    piece = _split(name)
    if piece and _usable_prefix(piece[0]):
        return _Parsed(prefixes=(piece[0],), ending=piece[1], compound=True)
    if piece:
        return _Parsed(ending=piece[1], compound=True)

    return _Parsed(prefixes=tuple(w for w in words if _usable_prefix(w)))


@dataclass
class KindModel:
    """What one kind of place sounds like in this world."""

    names: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()
    endings: tuple[str, ...] = ()
    article_rate: float = 0.0
    space_rate: float = 0.0
    of_heads: tuple[str, ...] = ()
    of_tails: tuple[str, ...] = ()
    of_rate: float = 0.0
    compound_rate: float = 0.0

    @property
    def compounds(self) -> bool:
        """Whether this world builds its place names out of parts at all."""
        return (bool(self.prefixes) and bool(self.endings)
                and self.compound_rate >= MIN_COMPOUND)


def _model(names: list[str], articles: int) -> KindModel:
    unique = sorted(set(names))
    if not unique:
        return KindModel()
    prefixes: set[str] = set()
    endings: list[str] = []
    of_heads: set[str] = set()
    of_tails: set[str] = set()
    of_count = compounds = 0
    for name in unique:
        read = _parse(name)
        prefixes.update(read.prefixes)
        if read.ending:
            endings.append(read.ending)
        if read.of_head:
            of_count += 1
            of_heads.add(read.of_head)
            if len(read.of_tail) >= 3:
                of_tails.add(read.of_tail)
        compounds += read.compound
    return KindModel(
        names=tuple(unique),
        prefixes=tuple(sorted(prefixes)),
        # Frequency first, so the world's habitual ending is its likeliest.
        endings=tuple(sorted(set(endings), key=lambda e: (-endings.count(e), e))),
        article_rate=articles / len(unique),
        space_rate=sum(1 for n in unique if " " in n and _parse(n).compound) / len(unique),
        of_heads=tuple(sorted(of_heads)),
        of_tails=tuple(sorted(of_tails)),
        of_rate=of_count / len(unique),
        compound_rate=compounds / len(unique),
    )


@dataclass
class Namer:
    """Names for generated things, in the register of the world that asked for them."""

    seed: str = "names"
    by_type: dict[str, KindModel] = field(default_factory=dict)
    places: KindModel = field(default_factory=KindModel)
    taken: set[str] = field(default_factory=set)
    _kinds: dict[str, KindModel] = field(default_factory=dict, repr=False)
    _chains: dict[str, dict[str, tuple[tuple[str, int], ...]]] = field(
        default_factory=dict, repr=False)

    # ---- learning ---------------------------------------------------------

    @classmethod
    def from_corpus(cls, corpus, *, seed: str = "names") -> Namer:
        """Learn from every name the writer has already chosen.

        `corpus` is `(type_key, name)` pairs — `WorldReading.corpus`. It used to be the
        world itself, walked here: the last traversal in the pipeline that opened the
        world for itself, and a stage that reads the world is a stage whose answer
        depends on when it ran rather than only on what it was given.
        """
        gathered: dict[str, list[str]] = {}
        articles: dict[str, int] = {}
        every: list[str] = []
        every_articles = 0
        taken: set[str] = set()
        for type_key, name in corpus:
            clean, had_article = _normalise(name)
            taken.add(clean)
            if len(clean) < 3:
                continue
            gathered.setdefault(type_key, []).append(clean)
            articles[type_key] = articles.get(type_key, 0) + had_article
            if type_key in PLACE_TYPES:
                every.append(clean)
                every_articles += had_article
        return cls(
            seed=seed,
            by_type={k: _model(v, articles.get(k, 0)) for k, v in sorted(gathered.items())},
            places=_model(every, every_articles),
            taken=taken,
        )

    def _kind(self, kind: str) -> KindModel:
        """The model for a kind, widening to the world's place names when too thin."""
        if kind in self._kinds:
            return self._kinds[kind]
        names: list[str] = []
        articles = 0
        for type_key in CORPORA.get(kind, (kind,)):
            model = self.by_type.get(type_key)
            if model:
                names.extend(model.names)
                articles += round(model.article_rate * len(model.names))
        model = _model(names, articles) if len(set(names)) >= MIN_CORPUS else self.places
        self._kinds[kind] = model
        return model

    # ---- generating -------------------------------------------------------

    def _ending_for(self, model: KindModel, hint: str, key: str) -> str:
        """An ending that says what the place is, preferring the world's own.

        A world that already writes -ford and -haven gets those. A world that writes
        neither is given a fitting one only when it has nothing of its own to say,
        because putting an unfamiliar ending on the map is putting words in the
        writer's mouth.
        """
        fitting = sorted(m for m, hints in MORPHEMES.items() if hint and hint in hints)
        theirs = [m for m in fitting if m in model.endings]
        if theirs:
            pool = theirs
        elif fitting:
            # The world has endings but none of them fit this ground. The ground wins:
            # a town on the only crossing for thirty miles should be a ford, and the
            # writer learns a word of their own world rather than losing the fact.
            pool = fitting
        else:
            # Nothing fits because the site has nothing to say. Use their commonest.
            pool = list(model.endings[:3])
        return self._pick(pool, f"ending|{key}")

    def _pick(self, pool: list[str], key: str) -> str:
        if not pool:
            return ""
        index = int(noise.unit(f"{self.seed}|{key}", len(pool)) * len(pool))
        return pool[min(index, len(pool) - 1)]

    def _compound(self, model: KindModel, key: str, hint: str, attempt: int) -> str:
        ending = self._ending_for(model, hint, key)
        prefix = self._pick(list(model.prefixes), f"prefix|{key}|{attempt}")
        if not prefix or not ending:
            return ""
        joined = _join(prefix, ending)
        spaced = noise.unit(f"{self.seed}|space|{key}", attempt) < model.space_rate
        return f"{prefix} {ending}" if spaced else joined

    def _chain(self, kind: str, model: KindModel) -> dict[str, tuple[tuple[str, int], ...]]:
        """The n-gram model, built once, with successors sorted so sampling is stable."""
        if kind in self._chains:
            return self._chains[kind]
        counts: dict[str, dict[str, int]] = {}
        for name in model.names:
            padded = BOUNDARY * ORDER + name + END
            for i in range(ORDER, len(padded)):
                context = padded[i - ORDER:i]
                counts.setdefault(context, {})
                counts[context][padded[i]] = counts[context].get(padded[i], 0) + 1
        chain = {context: tuple(sorted(successors.items()))
                 for context, successors in sorted(counts.items())}
        self._chains[kind] = chain
        return chain

    def _spin(self, kind: str, model: KindModel, key: str, attempt: int) -> str:
        chain = self._chain(kind, model)
        if not chain:
            return ""
        context = BOUNDARY * ORDER
        out: list[str] = []
        ended = False
        for step in range(MAX_LENGTH):
            successors = chain.get(context)
            if not successors:
                ended = True
                break
            total = sum(count for _, count in successors)
            pick = noise.unit(f"{self.seed}|{kind}|{key}|{attempt}", step) * total
            running = 0.0
            char = END
            for candidate, count in successors:
                running += count
                if pick < running:
                    char = candidate
                    break
            if char == END:
                ended = True
                break
            out.append(char)
            context = (context + char)[-ORDER:]
        # A walk the cap cut off mid-word is not a name — "The Norey Shorth S" is
        # fourteen characters of somewhere real. Returned empty so the attempt loop
        # tries again rather than shipping the stump.
        return "".join(out).strip() if ended else ""

    def name(self, kind: str, key: str, *, hint: str = "") -> str:
        """A name for one generated thing.

        `key` is what makes it stable: the same key in the same world always produces
        the same name, so regenerating a map does not rename the writer's towns.
        `hint` is why the thing is there — 'ford', 'harbour', 'pass', 'height' — and is
        what the ending is drawn from.

        Call sites must iterate in a sorted order: uniqueness is resolved in call order,
        so an unordered caller would shuffle the names between runs.
        """
        model = self._kind(kind)
        compounding = model.compounds
        for attempt in range(24):
            if compounding:
                candidate = self._compound(model, key, hint, attempt)
            else:
                candidate = self._spin(kind, model, key, attempt)
            flat = candidate.replace(" ", "")
            # The last-word floor guards against a low-order chain's fake endings:
            # with two characters of context, "Marsh" teaches that "sh" can end a
            # name, and "The Norey Sh" follows. No real toponym has a two-letter
            # final word.
            if (len(candidate) < 4 or candidate in self.taken
                    or candidate in model.names or flat in AVOID
                    or len(candidate.split()[-1]) < 3):
                continue
            self.taken.add(candidate)
            return self._dress(model, candidate, key)
        return self._dress(model, self._last_resort(model, key, hint), key)

    def _last_resort(self, model: KindModel, key: str, hint: str) -> str:
        """When the world is too small to have a voice — two entities, or none.

        Never with a number on the end. Greyhaven2 is not a place, it is a variable, and
        one of them on a map tells the writer the generator gave up. So the pool is
        widened instead of counted through: every stem the world offered against every
        ending it offered, in a stable order, and only then the key's own letters — which
        at least read as a word.
        """
        stems = list(model.prefixes or model.names or ("march",))
        endings = list(model.endings or ()) or [self._ending_for(model, hint, key)
                                                or "stead"]
        made: list[str] = []
        for stem in stems:
            for ending in tuple(endings) + _LAST_DITCH:
                candidate = _join(stem[:6], ending)
                made.append(candidate)
                if candidate not in self.taken:
                    self.taken.add(candidate)
                    return candidate

        # Every stem against every ending is spent. A world only reaches this by being
        # asked for far more places than it has words for, and the answer is the one the
        # language itself uses: say which of the two you mean.
        for qualifier in _QUALIFIERS:
            for base in made:
                candidate = f"{qualifier} {base}"
                if candidate not in self.taken:
                    self.taken.add(candidate)
                    return candidate
        return made[0] if made else "March"

    def _dress(self, model: KindModel, name: str, key: str) -> str:
        """Title case, and the article if this world's names of that kind wear one."""
        titled = " ".join(word.capitalize() for word in name.split())
        if noise.unit(f"{self.seed}|article|{key}") < model.article_rate:
            return f"The {titled}"
        return titled
