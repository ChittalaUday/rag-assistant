"""Hybrid retrieval orchestration: dense + sparse + graph -> fusion -> rerank -> selection."""

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from knowledge_engine.context import AppContext
from knowledge_engine.retrieval.fusion import fuse, rerank, select
from knowledge_engine.retrieval.graph import graph_candidates
from knowledge_engine.retrieval.models import Candidate, QueryPlan
from knowledge_engine.retrieval.structure import structural_neighbours
from knowledge_engine.text import clean_labels

Notify = Callable[[str, str], Awaitable[None]]
DENSE_K = 30
SPARSE_K = 30
CANDIDATES = 30
QUERY_CHARS = 650


@dataclass
class Retrieval:
    selected: list[Candidate] = field(default_factory=list)
    chunks: dict[str, dict[str, Any]] = field(default_factory=dict)
    relations: list[dict[str, Any]] = field(default_factory=list)  # relations the graph traversed
    trace: dict[str, Any] = field(default_factory=dict)


async def embed_query(ctx: AppContext, text: str, notify: Notify) -> list[float]:
    if ctx.model.lock.locked():
        await notify("waiting_for_model", "Waiting for the local model to finish its current task")

    async def started() -> None:
        await notify("embedding_query", "Embedding your question for source retrieval")

    vector = (
        await ctx.model.embed([ctx.settings.embedding_query_prefix + text[-QUERY_CHARS:]], started)
    )[0]
    if len(vector) != ctx.settings.embedding_dimensions or any(
        not math.isfinite(x) for x in vector
    ):
        raise ValueError("Query embedding returned an invalid vector")
    return vector


async def retrieve(
    ctx: AppContext, plan: QueryPlan, exclude_labels: list[str], notify: Notify
) -> Retrieval:
    store = ctx.store
    exclude_labels = clean_labels(exclude_labels)  # labels are stored cleaned; match the same way
    if store.chunks.count() == 0:
        return Retrieval()
    vector = await embed_query(ctx, plan.search_text, notify)
    await notify("retrieving", "Ranking source passages and supported relationships")
    dense = store.chunks.dense(vector, DENSE_K, exclude_labels)
    sparse = store.chunks.sparse(plan.terms, SPARSE_K, exclude_labels)
    pool = {c for c, _ in dense} | {c for c, _ in sparse}
    graph, relations = graph_candidates(
        store, plan, ctx.ontology, pool, exclude_labels=exclude_labels
    )
    rankings = {"dense": dense, "sparse": sparse, "graph": graph}
    candidates = fuse({k: v for k, v in rankings.items() if v})[: CANDIDATES + 10]
    chunks = {r["id"]: r for r in store.chunks.get_many([c.chunk_id for c in candidates])}
    excluded = set(exclude_labels)
    candidates = [
        c
        for c in candidates
        if c.chunk_id in chunks and not excluded & set(chunks[c.chunk_id]["labels"])
    ][:CANDIDATES]
    if not candidates:
        return Retrieval(trace={"dense": len(dense), "sparse": len(sparse), "graph": len(graph)})
    ids = [c.chunk_id for c in candidates]
    ranked = rerank(
        candidates,
        chunks,
        plan,
        store.chunks.doc_freq(plan.terms),
        store.chunks.count(),
        store.graph.mentions_in(ids),
    )
    selected = select(ranked, store.chunks.vectors(ids), plan.final_k)
    # a table or list cut across chunks travels whole (extras sit right next to what they complete)
    extra = structural_neighbours(store.chunks, {c.chunk_id: chunks[c.chunk_id] for c in selected})
    added: set[str] = set()
    expanded: list[Candidate] = []
    for c in selected:
        around = extra.get(c.chunk_id, [])
        before = [
            Candidate(r["id"]) for r in around if r["position"] < chunks[c.chunk_id]["position"]
        ]
        after = [
            Candidate(r["id"]) for r in around if r["position"] > chunks[c.chunk_id]["position"]
        ]
        chunks.update({r["id"]: r for r in around})
        expanded += [*before, c, *after]
        added |= {r["id"] for r in around}
    selected = expanded
    trace = {
        "dense": len(dense),
        "sparse": len(sparse),
        "graph": len(graph),
        "candidates": len(candidates),
        "selected": [
            {
                "chunk_id": c.chunk_id,
                "document": chunks[c.chunk_id]["document_name"],
                "score": round(c.final, 3),
                "ranks": c.ranks,
                **({"completes": "table or list"} if c.chunk_id in added else {}),
            }
            for c in selected
        ],
    }
    return Retrieval(selected, chunks, relations, trace)
