"""Graph retrieval: evidence for relations around the question's entities that bear on what was
asked, plus entity-mention chunks that text retrieval also found.

Two guards keep a well-connected entity (often the corpus's subject itself) from dragging in every
document that mentions it: a relation only counts when its predicate or its other endpoint relates
to the question's words, and mention-based chunks only reinforce chunks already found by search.
Hub entities are not expanded further.
"""

import math
from collections import defaultdict
from typing import Any

from knowledge_engine.extraction.ontology import Ontology
from knowledge_engine.retrieval.models import QueryPlan
from knowledge_engine.store import Store
from knowledge_engine.text import STOPWORDS, token_forms

MIN_CONFIDENCE = 0.5
HUB_DEGREE = 40
MENTION_LIMIT = 40
RELATION_LIMIT = 60


def related(forms: set[str], words: frozenset[str] | set[str]) -> int:
    """How many question words match (equal, or one a prefix of the other for stems like
    maintain/maintainer)."""
    return sum(
        any(
            f == w or (min(len(f), len(w)) >= 5 and (f.startswith(w) or w.startswith(f)))
            for w in words
        )
        for f in forms
    )


def graph_candidates(
    store: Store,
    plan: QueryPlan,
    ontology: Ontology,
    text_pool: set[str],
    limit: int = 30,
    exclude_labels: list[str] | None = None,
) -> tuple[list[tuple[str, float]], list[dict[str, Any]]]:
    """(ranked chunk ids with scores, the relevant relations that were traversed)."""
    seeds = {e.id for e in plan.seeds}
    if not seeds:
        return [], []
    seed_words = {f for e in plan.seeds for f in token_forms(e.name)}
    asked = {f for t in plan.terms for f in token_forms(t) if f not in STOPWORDS} - seed_words

    def relevance(r: dict[str, Any], around: set[str]) -> int:
        other = r["object_name"] if r["subject_id"] in around else r["subject_name"]
        words = set(ontology.keywords(r["predicate"])) | set(token_forms(other))
        return related(asked, words)

    scores: dict[str, float] = defaultdict(float)
    for chunk_id, mentions in store.graph.mention_chunks(list(seeds), MENTION_LIMIT):
        if chunk_id in text_pool:  # reinforce what search found; never inject
            scores[chunk_id] += 1.0 + math.log1p(mentions)
    weight: dict[str, float] = {}
    traversed: list[dict[str, Any]] = []
    frontier: set[str] = set()
    for r in store.graph.relations(
        list(seeds), MIN_CONFIDENCE, RELATION_LIMIT, exclude_labels=exclude_labels
    ):
        match = relevance(r, seeds)
        if match:
            weight[r["id"]] = r["confidence"] * (1 + 0.5 * (match - 1))
            traversed.append(r)
            frontier |= {r["subject_id"], r["object_id"]} - seeds
    if plan.hops >= 2 and frontier:
        degrees = store.graph.degrees(list(frontier))
        expand = [e for e in sorted(frontier) if degrees.get(e, 0) <= HUB_DEGREE][:25]
        found = (
            store.graph.relations(
                expand, MIN_CONFIDENCE, RELATION_LIMIT, exclude_labels=exclude_labels
            )
            if expand
            else []
        )
        for r in found:
            if r["id"] not in weight and (match := relevance(r, set(expand))):
                weight[r["id"]] = 0.5 * r["confidence"] * (1 + 0.5 * (match - 1))
                traversed.append(r)
    for ev in store.graph.evidence(list(weight), per_relation=2, exclude_labels=exclude_labels):
        scores[ev["chunk_id"]] += weight[ev["relation_id"]] * ev["confidence"]
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return ranked, traversed
