"""Entity resolution: turn surface names into canonical entity IDs shared across documents.

Order of evidence, strongest first: same normalised name, a recorded alias, then a conservative
fuzzy match. Short mentions inside one document ("Asha") join the single longer name that contains
them ("Asha Rao"). Anything ambiguous stays separate, so a wrong merge is rarer than a missed one.
IDs derive from the canonical class and key, so indexing the same documents again recreates the
same entities instead of duplicates.
"""

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from knowledge_engine.extraction.ontology import Ontology
from knowledge_engine.extraction.validator import Mention
from knowledge_engine.store.graph import GraphRepo
from knowledge_engine.text import STOPWORDS, has_legal_suffix, name_key

# Classes where a shorter form of a name inside one document normally means the same entity:
# people ("Asha" / "Asha Rao", any token subset) and organisations ("Niruthi" / "Niruthi Climate
# and Ecosystem", the short name must be a prefix). Products and projects never merge this way:
# "Beacon" and "Beacon firmware" are different things.
COREF_CLASSES = frozenset({"person", "organization"})
FUZZY_MIN_SIMILARITY = 0.85
FUZZY_MIN_LENGTH = 6
_EMPHASIS = "*_`"


@dataclass
class Resolution:
    ids: dict[tuple[str, str], str] = field(default_factory=dict)  # (class, key) -> entity id
    surfaces: dict[str, set[str]] = field(default_factory=dict)  # entity id -> spellings seen
    created: int = 0
    merged: int = 0  # mentions folded into an existing entity or a longer name


def entity_id(cls: str, key: str) -> str:
    return "ent_" + hashlib.sha1(f"{cls}|{key}".encode()).hexdigest()[:16]


_INITIAL_SKIP = frozenset({"and", "of", "the", "for", "in", "on", "a", "an"})
# classes of named things whose short form may be an acronym of a longer name
ACRONYM_CLASSES = frozenset({"organization", "thing", "event", "document"})


def initials(key: str) -> str:
    return "".join(t[0] for t in key.split() if t not in _INITIAL_SKIP)


def _is_acronym_of(spellings: Counter[str], long_key: str) -> bool:
    """The short name is, on its own, an all-caps word spelling the initials of the long name
    ("TOPS" / "Terrestrial Observation and Prediction System"). A short name that merely contains
    the acronym ("AWS Lambda", "AWS S3" next to "Amazon Web Services") names something else."""
    wanted = initials(long_key)
    return (
        len(long_key.split()) >= 3
        and len(wanted) >= 2
        and any(spelling.isupper() and spelling.casefold() == wanted for spelling in spellings)
    )


def _contained(cls: str, short: str, long: str) -> bool:
    """Is `short` a shorter form of `long`? Organisations: a prefix; people: any token subset."""
    if cls == "organization":
        return long.startswith(short + " ")
    return set(short.split()) < set(long.split())


def _distinctive(key: str) -> bool:
    tokens = key.split()
    return len(tokens) > 1 or (len(tokens[0]) >= 4 and tokens[0] not in STOPWORDS)


def _digits(key: str) -> list[str]:
    return [t for t in key.split() if any(c.isdigit() for c in t)]


def _fuzzy_ok(a: str, b: str, similarity: float) -> bool:
    return (
        similarity >= FUZZY_MIN_SIMILARITY
        and min(len(a), len(b)) >= FUZZY_MIN_LENGTH
        and _digits(a) == _digits(b)
    )


def resolve(graph: GraphRepo, ontology: Ontology, mentions: list[Mention]) -> Resolution:
    spellings: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    etype: dict[tuple[str, str], str] = {}
    for m in mentions:
        t = ontology.types[m.type]
        key = name_key(m.name)
        if key:
            spellings[(t.cls, key)][m.name] += 1
            etype.setdefault((t.cls, key), t.name)

    # in-document coreference: a key whose tokens sit inside exactly one longer key joins it
    parent: dict[tuple[str, str], tuple[str, str]] = {}
    for cls in {c for c, _ in spellings}:
        keys = [k for c, k in spellings if c == cls]
        if cls not in COREF_CLASSES:
            continue
        for k in keys:
            supers = [o for o in keys if o != k and _contained(cls, k, o)]
            complete = cls == "organization" and any(
                has_legal_suffix(s) for s in spellings[(cls, k)]
            )
            if len(supers) == 1 and _distinctive(k) and not complete:  # "Apple Inc." is not
                parent[(cls, k)] = (cls, supers[0])  # short for "Apple Valley School District"

    # acronym and its expansion are one entity; ambiguous expansions are left alone
    for cls, key in list(spellings):
        if cls not in ACRONYM_CLASSES or (cls, key) in parent:
            continue
        longs = [
            k
            for c, k in spellings
            if c == cls and k != key and _is_acronym_of(spellings[(cls, key)], k)
        ]
        if len(longs) == 1:
            parent[(cls, key)] = (cls, longs[0])

    def root(node: tuple[str, str]) -> tuple[str, str]:
        while node in parent:
            node = parent[node]
        return node

    members: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for node in spellings:
        members[root(node)].append(node)

    out = Resolution()
    for (cls, key), group in sorted(members.items()):
        group_keys = [key] + sorted(k for _, k in group if k != key)
        exact = graph.exact(cls, group_keys)
        hit: str | None = None
        for k in group_keys:
            candidates = {i for i in exact.get(k, [])}
            if len(candidates) == 1:
                hit = next(iter(candidates))
                break
        if hit is None:
            best = max(graph.similar(cls, key), key=lambda r: r["sim"], default=None)
            if best and _fuzzy_ok(key, best["key"], float(best["sim"])):
                hit = str(best["id"])
        own = spellings[(cls, key)]
        canonical = max(own, key=lambda s: (own[s], len(s)))
        if hit is None:
            hit = entity_id(cls, key)
            graph.create_entity(hit, etype[(cls, key)], cls, canonical, key)
            out.created += 1
        else:
            out.merged += 1
        all_spellings: set[str] = set()
        aliases: list[tuple[str, str]] = []
        for node in group:
            for spelling in spellings[node]:
                all_spellings.add(spelling)
                aliases.append((spelling, name_key(spelling)))
            out.ids[node] = hit
            if node != (cls, key):
                out.merged += 1
        graph.add_aliases(hit, aliases)
        out.surfaces.setdefault(hit, set()).update(all_spellings)
    return out


def scan_mentions(
    chunks: list[tuple[str, str]], surfaces: dict[str, set[str]]
) -> list[tuple[str, str, int]]:
    """Count where each resolved entity is mentioned: (entity id, chunk id, count) rows.

    Scans every chunk of the document for every spelling, so a chunk is linked to an entity even
    when the model only extracted it from a neighbouring chunk.
    """
    by_spelling: dict[str, str] = {}
    for eid, names in surfaces.items():
        for name in names:
            by_spelling.setdefault(_fold(name), eid)
    if not by_spelling:
        return []
    sep = r"[\s" + re.escape(_EMPHASIS) + r"]+"
    alternatives = sorted(by_spelling, key=len, reverse=True)
    pattern = re.compile(
        r"(?<!\w)(?:"
        + "|".join(sep.join(map(re.escape, a.split())) for a in alternatives)
        + r")(?!\w)",
        re.IGNORECASE,
    )
    rows: list[tuple[str, str, int]] = []
    for chunk_id, text in chunks:
        counts: Counter[str] = Counter()
        for m in pattern.finditer(text):
            found = by_spelling.get(_fold(m.group(0)))
            if found:
                counts[found] += 1
        rows += [(eid, chunk_id, n) for eid, n in counts.items()]
    return rows


def _fold(text: str) -> str:
    return re.sub(r"[\s" + re.escape(_EMPHASIS) + r"]+", " ", text).strip().casefold()
