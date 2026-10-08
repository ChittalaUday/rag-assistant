"""Deterministic validation of model extractions. Model output is untrusted until it passes here.

Rules enforced (nothing is inferred or "recovered"):
- every entity name must appear in the chunk and look like a name for named types;
- every relation must name two extracted entities, use a known predicate whose subject/object
  types are allowed (a reversed pair is flipped when that makes it valid), and not be a self-loop;
- every relation must carry a quote that is a verbatim span of the chunk containing both endpoints,
  stored with character offsets so the evidence can be re-verified against the source text;
- extraction confidence is recorded per piece of evidence, separately from truth.
"""

import re
from dataclasses import dataclass, field

from pydantic import BaseModel

from knowledge_engine.extraction.ontology import FALLBACK_PREDICATE, Ontology
from knowledge_engine.text import name_key

MAX_NAME_WORDS = 9
MAX_QUOTE_CHARS = 400
_EMPHASIS = "*_`"


class RawEntity(BaseModel):
    name: str = ""
    type: str = ""


class RawRelation(BaseModel):
    subject: str = ""
    predicate: str = ""
    object: str = ""
    phrase: str = ""
    quote: str = ""


class RawExtraction(BaseModel):
    entities: list[RawEntity] = []
    relations: list[RawRelation] = []


@dataclass(frozen=True)
class Mention:
    name: str
    type: str


@dataclass(frozen=True)
class FoundRelation:
    subject: str
    subject_type: str
    predicate: str
    object: str
    object_type: str
    phrase: str | None
    quote: str  # verbatim slice of the chunk text
    quote_start: int  # offsets within the chunk text
    quote_end: int
    confidence: float


@dataclass
class ChunkExtraction:
    entities: list[Mention] = field(default_factory=list)
    relations: list[FoundRelation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def clean_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value.strip(" \t\"'`*_()[]{}<>.,;:!?“”‘’")


def locate(text: str, needle: str) -> tuple[int, int, bool] | None:
    """Find needle in text: (start, end, exact). Tolerates case, whitespace and markdown emphasis
    differences, so a model that drops '**' or re-wraps lines still yields a real source span."""
    needle = needle.strip().strip('"“”‘’')
    if not needle:
        return None
    i = text.find(needle)
    if i >= 0:
        return i, i + len(needle), True
    tokens = [t for t in re.findall(r"\w+|[^\w\s]", needle) if t not in _EMPHASIS]
    if not tokens:
        return None
    emphasis = re.escape(_EMPHASIS)
    parts = [re.escape(tokens[0])]
    for prev, tok in zip(tokens, tokens[1:], strict=False):
        both_words = prev[-1].isalnum() and tok[0].isalnum()
        parts.append(f"[\\s{emphasis}]{'+' if both_words else '*'}" + re.escape(tok))
    pattern = f"[{emphasis}]*" + "".join(parts) + f"[{emphasis}]*"
    m = re.search(pattern, text, re.IGNORECASE)
    return (m.start(), m.end(), False) if m else None


def _name_pattern(name: str) -> re.Pattern[str] | None:
    words = [w for w in name.split() if w]
    if not words:
        return None
    sep = r"[\s" + re.escape(_EMPHASIS) + r"]+"
    return re.compile(r"(?<!\w)" + sep.join(re.escape(w) for w in words) + r"(?!\w)", re.IGNORECASE)


def find_name(text: str, name: str) -> str | None:
    """The source spelling of a name inside text (whole-word, tolerant), or None."""
    pattern = _name_pattern(name)
    m = pattern.search(text) if pattern else None
    return m.group(0) if m else None


MAX_HEADING_LINES = 6  # how far above a sentence its section heading may be


def _heading_above(text: str, start: int, pattern: re.Pattern[str]) -> tuple[int, int] | None:
    """The nearest markdown heading above `start` that names the entity: a section states its topic
    in the heading and then says facts about it without repeating the name."""
    end = text.rfind("\n", 0, start)
    for _ in range(MAX_HEADING_LINES):
        if end < 0:
            return None
        line_start = text.rfind("\n", 0, end) + 1
        line = text[line_start:end]
        if line.lstrip().startswith("#") and (m := pattern.search(line)):
            return line_start + m.start(), line_start + m.end()
        end = line_start - 1
    return None


def cover_names(text: str, start: int, end: int, names: list[str]) -> tuple[int, int] | None:
    """Grow the span [start, end) just enough to include every name, using the nearest mention on
    the same line (bullets state the subject first and the facts after) or, failing that, the
    section heading above (the span then runs from the heading to the sentence). The span must
    already contain one of the names. None if impossible."""
    if not any(find_name(text[start:end], n) for n in names):
        return None  # a quote naming neither entity is unrelated, not merely incomplete
    line_start = text.rfind("\n", 0, start) + 1
    nl = text.find("\n", end)
    line = text[line_start : nl if nl >= 0 else len(text)]
    for name in names:
        if find_name(text[start:end], name):
            continue
        pattern = _name_pattern(name)
        spans = (
            [(line_start + m.start(), line_start + m.end()) for m in pattern.finditer(line)]
            if pattern
            else []
        )
        if not spans and pattern and (heading := _heading_above(text, start, pattern)):
            spans = [heading]
        if not spans:
            return None
        a, b = min(spans, key=lambda ab: max(start - ab[1], ab[0] - end, 0))
        start, end = min(start, a), max(end, b)
    return (start, end) if end - start <= MAX_QUOTE_CHARS else None


def _looks_proper(spelling: str) -> bool:
    return any(c.isupper() or c.isdigit() for c in spelling)


def validate_extraction(raw: RawExtraction, text: str, ontology: Ontology) -> ChunkExtraction:
    out = ChunkExtraction()
    warnings: set[str] = set()
    has_capitals = any(c.isupper() for c in text)
    kept: dict[str, Mention] = {}  # name key -> mention
    for e in raw.entities:
        name = clean_name(e.name)
        etype = ontology.entity_type(e.type)
        key = name_key(name)
        if not key or len(name) < 2 or len(name.split()) > MAX_NAME_WORDS:
            warnings.add("Rejected entity with an unusable name")
            continue
        if etype is None:
            warnings.add("Rejected entity with a type outside the ontology")
            continue
        spelling = find_name(text, name)
        if spelling is None:
            warnings.add("Rejected entities whose names are absent from source text")
            continue
        if etype.standalone and has_capitals and not _looks_proper(spelling):
            warnings.add("Rejected common noun typed as a named entity")
            continue
        kept.setdefault(key, Mention(name, etype.name))

    seen: set[tuple[str, str, str, int]] = set()
    in_relation: set[str] = set()
    for r in raw.relations:
        predicate = ontology.predicate(r.predicate)
        s_key, o_key = name_key(clean_name(r.subject)), name_key(clean_name(r.object))
        if predicate is None:
            warnings.add("Rejected relationship with a predicate outside the ontology")
            continue
        subject, obj = kept.get(s_key), kept.get(o_key)
        if subject is None or obj is None:
            warnings.add("Rejected relationship whose endpoints are not extracted entities")
            continue
        if s_key == o_key:
            warnings.add("Rejected self-referential relationship")
            continue
        if not ontology.allows(predicate, subject.type, obj.type):
            if not predicate.symmetric and ontology.allows(predicate, obj.type, subject.type):
                subject, obj, s_key, o_key = obj, subject, o_key, s_key
            else:
                warnings.add("Rejected relationship violating ontology type constraints")
                continue
        if predicate.name == FALLBACK_PREDICATE and not (
            ontology.types[subject.type].standalone and ontology.types[obj.type].standalone
        ):
            warnings.add("Rejected generic relationship between unnamed concepts")
            continue
        found = locate(text, r.quote)
        if found is None:
            warnings.add("Rejected relationship with non-verbatim evidence")
            continue
        start, end, exact = found
        covered = cover_names(text, start, end, [subject.name, obj.name])
        if covered is None:
            warnings.add("Rejected relationship whose evidence lacks both endpoints")
            continue
        exact = exact and covered == (start, end)
        start, end = covered
        quote = text[start:end]
        phrase = clean_name(r.phrase)
        phrase_found = bool(phrase) and locate(quote, phrase) is not None
        confidence = 0.45
        confidence += 0.15 if phrase_found else 0.0
        confidence += 0.0 if predicate.name == FALLBACK_PREDICATE else 0.15
        confidence += 0.10 if exact else 0.0
        confidence += 0.10 if ontology.is_strict(predicate) else 0.0
        marker = (s_key, predicate.name, o_key, start)
        if marker in seen:
            continue
        seen.add(marker)
        in_relation.update((s_key, o_key))
        out.relations.append(
            FoundRelation(
                subject=subject.name,
                subject_type=subject.type,
                predicate=predicate.name,
                object=obj.name,
                object_type=obj.type,
                phrase=phrase if phrase_found else None,
                quote=quote,
                quote_start=start,
                quote_end=end,
                confidence=round(min(confidence, 0.95), 2),
            )
        )
    for key, mention in kept.items():
        if key in in_relation or ontology.types[mention.type].standalone:
            out.entities.append(mention)
    out.warnings = sorted(warnings)
    return out
