"""Ontology: the entity types and typed relations a corpus is described with.

Loaded from TOML so a different domain only needs a different file. It drives three things:
the structured-output schema and prompt sent to the model, the deterministic validation of what
comes back, and entity identity classes used when merging mentions into canonical entities.
"""

import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from knowledge_engine.text import STOPWORDS, token_forms

DEFAULT_ONTOLOGY = Path(__file__).with_name("ontology.toml")
FALLBACK_PREDICATE = "related_to"


@dataclass(frozen=True)
class EntityType:
    name: str
    description: str
    cls: str
    standalone: bool
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class Predicate:
    name: str
    label: str
    description: str
    domain: frozenset[str]  # empty = any type
    range: frozenset[str]
    symmetric: bool
    functional: bool


@dataclass(frozen=True)
class Ontology:
    types: dict[str, EntityType]
    predicates: dict[str, Predicate]
    version: str
    examples: tuple[dict[str, Any], ...] = ()  # worked few-shot passages; empty = zero-shot

    def keywords(self, predicate: str) -> frozenset[str]:
        """Content words that signal this predicate in a question (its label and description)."""
        p = self.predicates.get(predicate)
        if p is None:
            return frozenset()
        return frozenset(
            f
            for f in token_forms(f"{p.label} {p.description}")
            if f not in STOPWORDS and len(f) > 2
        )

    def entity_type(self, name: str) -> EntityType | None:
        """Resolve a model-supplied type string (case-insensitive, via aliases) to a type."""
        wanted = name.strip().casefold().replace("_", " ")
        for t in self.types.values():
            if wanted == t.name.casefold() or wanted in t.aliases:
                return t
        return None

    def predicate(self, name: str) -> Predicate | None:
        return self.predicates.get(name.strip().casefold().replace(" ", "_"))

    def allows(self, predicate: Predicate, subject: str, obj: str) -> bool:
        return (not predicate.domain or subject in predicate.domain) and (
            not predicate.range or obj in predicate.range
        )

    def is_strict(self, predicate: Predicate) -> bool:
        """True when the predicate constrains both ends (a type match is then real evidence)."""
        return bool(predicate.domain) and bool(predicate.range)

    def extraction_schema(self, max_items: int = 20) -> dict[str, Any]:
        """JSON schema for constrained decoding: types and predicates are closed enums."""
        text = {"type": "string", "minLength": 1}
        entity_items = {
            "type": "object",
            "properties": {
                "name": {**text, "maxLength": 120},
                "type": {"type": "string", "enum": list(self.types)},
            },
            "required": ["name", "type"],
        }
        relation_items = {
            "type": "object",
            "properties": {
                "subject": {**text, "maxLength": 120},
                "predicate": {"type": "string", "enum": list(self.predicates)},
                "object": {**text, "maxLength": 120},
                "phrase": {"type": "string", "maxLength": 120},
                "quote": {**text, "maxLength": 300},
            },
            "required": ["subject", "predicate", "object", "phrase", "quote"],
        }
        # Relations come first: the model states the facts, then lists every endpoint (and other
        # named things) as typed entities. Listing endpoints afterwards recovers abstract objects
        # such as "leave request" that models skip when asked for entities up front.
        return {
            "type": "object",
            "properties": {
                "relations": {"type": "array", "maxItems": max_items, "items": relation_items},
                "entities": {"type": "array", "maxItems": max_items, "items": entity_items},
            },
            "required": ["relations", "entities"],
        }

    def prompt_guide(self) -> str:
        types = "\n".join(f"- {t.name}: {t.description}" for t in self.types.values())

        def ends(s: frozenset[str]) -> str:
            return "|".join(sorted(s)) if s else "any"

        relations = "\n".join(
            f"- {p.name}: {p.description} (subject {ends(p.domain)}; object {ends(p.range)})"
            for p in self.predicates.values()
        )
        return (
            f"ENTITY TYPES (pick exactly one):\n{types}\n\n"
            f"RELATIONS (pick the closest):\n{relations}"
        )

    def example_guide(self) -> str:
        """The worked examples as prompt text, relations before entities like the schema; empty
        when the ontology has none."""
        return "\n\n".join(
            f"EXAMPLE {n} passage:\n{ex['passage']}\nEXAMPLE {n} answer:\n"
            + json.dumps({"relations": ex["relations"], "entities": ex["entities"]})
            for n, ex in enumerate(self.examples, 1)
        )


def _check_example(ontology: Ontology, n: int, ex: dict[str, Any]) -> None:
    """An example that contradicts the ontology would teach the model what validation rejects."""
    try:
        passage, entities, relations = ex["passage"], ex["entities"], ex["relations"]
        listed = {e["name"]: e["type"] for e in entities}
        for e in entities:
            if e["type"] not in ontology.types:
                raise ValueError(f"unknown entity type {e['type']!r}")
        for r in relations:
            predicate = ontology.predicates.get(r["predicate"])
            if predicate is None:
                raise ValueError(f"unknown predicate {r['predicate']!r}")
            for end in (r["subject"], r["object"]):
                if end not in listed:
                    raise ValueError(f"{end!r} is not listed in its entities")
            if r["quote"] not in passage:
                raise ValueError(f"quote {r['quote']!r} is not verbatim in the passage")
            if r["phrase"] not in r["quote"]:
                raise ValueError(f"phrase {r['phrase']!r} is not inside its quote")
            if not ontology.allows(predicate, listed[r["subject"]], listed[r["object"]]):
                raise ValueError(
                    f"{r['predicate']} may not connect {listed[r['subject']]} to "
                    f"{listed[r['object']]}"
                )
    except KeyError as missing:
        raise ValueError(f"Example {n} is missing {missing}") from None
    except ValueError as problem:
        raise ValueError(f"Example {n}: {problem}") from None


def load_ontology(path: Path | None = None) -> Ontology:
    raw = tomllib.loads((path or DEFAULT_ONTOLOGY).read_text(encoding="utf-8"))
    types = {
        name: EntityType(
            name=name,
            description=spec["description"],
            cls=spec.get("class", name.casefold()),
            standalone=bool(spec.get("standalone", False)),
            aliases=tuple(a.casefold() for a in spec.get("aliases", [])),
        )
        for name, spec in raw["types"].items()
    }
    predicates: dict[str, Predicate] = {}
    for name, spec in raw["predicates"].items():
        domain, rng = frozenset(spec.get("domain", [])), frozenset(spec.get("range", []))
        unknown = (domain | rng) - set(types)
        if unknown:
            raise ValueError(f"Predicate {name} references unknown types: {sorted(unknown)}")
        predicates[name] = Predicate(
            name=name,
            label=spec["label"],
            description=spec["description"],
            domain=domain,
            range=rng,
            symmetric=bool(spec.get("symmetric", False)),
            functional=bool(spec.get("functional", False)),
        )
    if FALLBACK_PREDICATE not in predicates:
        raise ValueError(f"Ontology must define the fallback predicate {FALLBACK_PREDICATE!r}")
    # The version covers the vocabulary; examples change the prompt, which is part of the cache key.
    vocabulary = {k: v for k, v in raw.items() if k != "examples"}
    digest = hashlib.sha256(json.dumps(vocabulary, sort_keys=True).encode()).hexdigest()[:10]
    examples = tuple(raw.get("examples", []))
    ontology = Ontology(types=types, predicates=predicates, version=digest, examples=examples)
    for n, ex in enumerate(examples, 1):
        _check_example(ontology, n, ex)
    return ontology
