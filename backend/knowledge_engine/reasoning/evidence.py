"""Evidence building: turn retrieved chunks into the citable passages, graph facts, conflicts and
connections that the answer is generated from."""

from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from knowledge_engine.context import AppContext
from knowledge_engine.reasoning.contradictions import Conflict, find_conflicts
from knowledge_engine.reasoning.paths import describe, find_path
from knowledge_engine.retrieval.models import QueryPlan
from knowledge_engine.retrieval.search import Retrieval

MAX_FACTS = 24
MAX_PATHS = 4


@dataclass
class Source:
    alias: str  # S1, S2 ... short ids the model cites
    chunk_id: str
    document_name: str
    heading: str | None
    text: str  # what the model sees (overlap with the previous passage trimmed)
    labels: list[str]
    rank: int  # retrieval rank, 1 = best


@dataclass
class Fact:
    relation_id: str
    subject: str
    predicate: str
    label: str
    object: str
    quote: str
    aliases: list[str]
    subject_id: str
    object_id: str
    confidence: float


@dataclass
class Evidence:
    sources: list[Source] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    connections: list[str] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)  # every relation considered


def build_evidence(ctx: AppContext, plan: QueryPlan, retrieval: Retrieval) -> Evidence:
    ev = Evidence()
    # keep the best-ranked passages that fit the budget, then show them in reading order
    chosen: list[tuple[int, dict[str, Any]]] = []
    used = 0
    for rank, cand in enumerate(retrieval.selected, 1):
        row = retrieval.chunks[cand.chunk_id]
        size = len(row["text"]) + len(row["document_name"]) + 40
        if chosen and used + size > ctx.settings.context_chars:
            continue
        chosen.append((rank, row))
        used += size
    first_seen = {r["document_id"]: i for i, (_, r) in reversed(list(enumerate(chosen)))}
    chosen.sort(key=lambda t: (first_seen[t[1]["document_id"]], t[1]["position"]))
    previous: dict[str, Any] | None = None
    for n, (rank, row) in enumerate(chosen, 1):
        text = row["text"]
        if (
            previous
            and previous["document_id"] == row["document_id"]
            and previous["char_end"] is not None
            and row["char_start"] is not None
            and 0 < previous["char_end"] - row["char_start"] < len(text)
        ):
            text = text[previous["char_end"] - row["char_start"] :]  # overlap already shown
        ev.sources.append(
            Source(
                f"S{n}",
                row["id"],
                row["document_name"],
                row["heading"],
                text,
                list(row["labels"]),
                rank,
            )
        )
        previous = row

    alias_of = {s.chunk_id: s.alias for s in ev.sources}
    labels = {name: p.label for name, p in ctx.ontology.predicates.items()}
    wanted = {e.id for e in plan.entities}
    question = plan.search_text.casefold()
    facts: dict[str, Fact] = {}
    relations: dict[str, dict[str, Any]] = {r["id"]: r for r in retrieval.relations}
    for row in ctx.store.graph.evidence_in_chunks(list(alias_of)):
        rel = {
            "id": row["relation_id"],
            "subject_id": row["subject_id"],
            "object_id": row["object_id"],
            "predicate": row["predicate"],
            "subject_name": row["subject_name"],
            "object_name": row["object_name"],
        }
        relations.setdefault(rel["id"], rel)
        fact = facts.get(row["relation_id"])
        if fact is None:
            label = labels.get(row["predicate"], row["predicate"])  # ontology may have changed
            facts[row["relation_id"]] = Fact(
                row["relation_id"], row["subject_name"], row["predicate"], label,
                row["object_name"], row["quote"], [alias_of[row["chunk_id"]]], row["subject_id"],
                row["object_id"], float(row["confidence"]),
            )  # fmt: skip
        elif alias_of[row["chunk_id"]] not in fact.aliases:
            fact.aliases.append(alias_of[row["chunk_id"]])

    def relevance(f: Fact) -> float:
        touches = bool(wanted & {f.subject_id, f.object_id})
        named = f.subject.casefold() in question or f.object.casefold() in question
        return 2.0 * touches + 1.0 * named + f.confidence

    ev.facts = sorted(facts.values(), key=lambda f: (-relevance(f), f.relation_id))[:MAX_FACTS]
    ev.relations = list(relations.values())
    functional = {n for n, p in ctx.ontology.predicates.items() if p.functional}
    ev.conflicts = find_conflicts(
        [r for r in ev.relations if "subject_name" in r and "object_name" in r], functional
    )
    if len(wanted) >= 2:
        for a, b in combinations(sorted(wanted), 2):
            path = find_path(ev.relations, a, b)
            if path and len(ev.connections) < MAX_PATHS:
                ev.connections.append(describe(path, labels))
    return ev
