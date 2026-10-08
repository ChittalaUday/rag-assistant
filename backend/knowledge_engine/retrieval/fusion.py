"""Combine retrievers: reciprocal rank fusion, feature reranking and duplicate-free selection.

The reranker is deliberately a transparent feature model, not a learned one: no cross-encoder is
available locally. Each feature is stored on the candidate so a ranking can be explained.
"""

import math
from typing import Any

import numpy as np

from knowledge_engine.retrieval.models import Candidate, QueryPlan
from knowledge_engine.text import token_forms

RRF_K = 60
WEIGHTS = {"dense": 1.0, "sparse": 1.0, "graph": 0.8}
FEATURE_WEIGHTS = {"rrf": 0.45, "coverage": 0.25, "entity": 0.20, "dense": 0.10}
DUPLICATE_SIMILARITY = 0.97


def fuse(rankings: dict[str, list[tuple[str, float]]]) -> list[Candidate]:
    """Reciprocal rank fusion over the retrievers' ranked lists."""
    pool: dict[str, Candidate] = {}
    for name, ranked in rankings.items():
        for rank, (chunk_id, score) in enumerate(ranked, 1):
            c = pool.setdefault(chunk_id, Candidate(chunk_id))
            c.ranks[name], c.scores[name] = rank, score
            c.rrf += WEIGHTS.get(name, 1.0) / (RRF_K + rank)
    return sorted(pool.values(), key=lambda c: (-c.rrf, c.chunk_id))


def _idf(df: int, n: int) -> float:
    return math.log(1 + (n - df + 0.5) / (df + 0.5))


def rerank(
    candidates: list[Candidate],
    chunks: dict[str, dict[str, Any]],
    plan: QueryPlan,
    doc_freq: dict[str, int],
    total_chunks: int,
    mentions: dict[str, set[str]],
) -> list[Candidate]:
    """Score candidates on fused rank, IDF-weighted term coverage, entity coverage, similarity."""
    if not candidates:
        return []
    # a term found in no chunk (or a database stop word) can match nothing: weighting it would
    # only dilute the coverage of the terms that can
    forms = {t: token_forms(t)[0] for t in plan.terms if token_forms(t) and doc_freq.get(t, 0) > 0}
    weights = {t: _idf(doc_freq.get(t, 0), max(total_chunks, 1)) for t in forms}
    total_weight = sum(weights.values()) or 1.0
    top_rrf = max(c.rrf for c in candidates) or 1.0
    dense = [c.scores["dense"] for c in candidates if "dense" in c.scores]
    lo, hi = (min(dense), max(dense)) if dense else (0.0, 0.0)
    wanted = {e.id for e in plan.seeds}
    for c in candidates:
        row = chunks[c.chunk_id]
        present = set(token_forms(f"{row['context']} {row['text']}"))
        covered = sum(w for t, w in weights.items() if forms[t] in present)
        c.features = {
            "rrf": c.rrf / top_rrf,
            "coverage": covered / total_weight,
            "entity": len(wanted & mentions.get(c.chunk_id, set())) / len(wanted)
            if wanted
            else 0.0,
            "dense": (c.scores["dense"] - lo) / (hi - lo)
            if "dense" in c.scores and hi > lo
            else 0.0,
        }
        c.final = sum(FEATURE_WEIGHTS[k] * v for k, v in c.features.items())
    return sorted(candidates, key=lambda c: (-c.final, c.chunk_id))


def select(ranked: list[Candidate], vectors: dict[str, list[float]], k: int) -> list[Candidate]:
    """Take the top k in score order, skipping near-duplicates (such as the repeated rows of a
    form) of passages already taken. A diversity penalty (MMR) was measured and removed: on a
    populated index it only pushed second documents of cross-document questions out of the top k."""
    picked: list[Candidate] = []
    units: list[np.ndarray[Any, Any]] = []
    for c in ranked:
        v = np.asarray(vectors.get(c.chunk_id, [0.0]), dtype=float)
        unit = v / np.linalg.norm(v) if np.linalg.norm(v) else v
        if any(float(unit @ u) >= DUPLICATE_SIMILARITY for u in units):
            continue
        picked.append(c)
        units.append(unit)
        if len(picked) == k:
            break
    return picked
