"""Extraction evaluation: how many hand-labelled facts does the extractor + validator recover?

Runs chunking, the LLM extractor and the deterministic validator on the labelled documents (no
database), then compares the validated relations with the gold triples. Gold is a set of core
facts, not exhaustive, so extra relations are listed for manual review rather than counted wrong.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from knowledge_engine.extraction.extractor import Extractor
from knowledge_engine.extraction.ontology import Ontology
from knowledge_engine.extraction.validator import FoundRelation, validate_extraction
from knowledge_engine.ingestion.chunker import chunk_document
from knowledge_engine.ingestion.quality import extractable
from knowledge_engine.pipeline.indexing import document_title
from knowledge_engine.text import name_matches, normalize_text


class FileCache:
    """JSON-file extraction cache so repeated evaluation runs only pay for changed prompts."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}

    def get(self, key: str) -> dict[str, Any] | None:
        value = self.data.get(key)
        return dict(value) if value is not None else None

    def put(self, key: str, payload: dict[str, Any]) -> None:
        self.data[key] = payload
        self.path.write_text(json.dumps(self.data))


@dataclass
class DocResult:
    gold: int = 0
    pair_hits: int = 0
    triple_hits: int = 0
    extracted: int = 0
    extracted_in_gold: int = 0
    missed: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)


def _variants(value: str) -> list[str]:
    return [v for v in value.split("|") if v]


def score(found: list[tuple[str, str | None, str]], gold: list[list[str]]) -> DocResult:
    """Compare (subject, predicate or None, object) triples with gold triples."""
    result = DocResult(gold=len(gold), extracted=len(found))
    matched_found: set[int] = set()

    def pair_match(i: int, subjects: list[str], objects: list[str]) -> bool:
        a, _, b = found[i]

        def hit(x: str, y: str) -> bool:
            return any(name_matches(x, s) for s in subjects) and any(
                name_matches(y, o) for o in objects
            )

        return hit(a, b) or hit(b, a)

    for s, p, o in gold:
        subjects, objects, predicates = _variants(s), _variants(o), p.split("/")
        pair = [i for i in range(len(found)) if pair_match(i, subjects, objects)]
        if pair:
            result.pair_hits += 1
            matched_found.update(pair)
            if any(found[i][1] in predicates for i in pair):
                result.triple_hits += 1
            else:
                got = sorted({str(found[i][1]) for i in pair})
                result.missed.append(f"predicate: {s} -[{p}]-> {o}  (got {got})")
        else:
            result.missed.append(f"missing:   {s} -[{p}]-> {o}")
    result.extracted_in_gold = len(matched_found)
    result.extra = [
        f"{a} -[{p}]-> {b}" for i, (a, p, b) in enumerate(found) if i not in matched_found
    ]
    return result


async def evaluate_document(
    extractor: Extractor, ontology: Ontology, name: str, text: str, gold: list[list[str]]
) -> tuple[DocResult, list[FoundRelation]]:
    text = normalize_text(text)
    chunks = chunk_document(text)
    title = document_title(name)
    found: list[FoundRelation] = []
    for chunk, ok in zip(chunks, extractable(chunks), strict=True):
        if ok:
            try:
                raw = await extractor.extract(title, chunk)
            except ValueError:
                continue
            found += validate_extraction(raw, chunk.text, ontology).relations
    triples = [(r.subject, r.predicate, r.object) for r in found]
    return score(list(dict.fromkeys(triples)), gold), found
