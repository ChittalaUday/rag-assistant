"""Hybrid retrieval orchestration: dense + sparse -> fusion -> rerank -> selection -> second hop."""

import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from knowledge_engine.context import AppContext
from knowledge_engine.retrieval.fusion import fuse, rerank, select
from knowledge_engine.retrieval.hop import second_hop
from knowledge_engine.retrieval.models import Candidate, QueryPlan
from knowledge_engine.retrieval.structure import structural_neighbours
from knowledge_engine.text import clean_labels

log = logging.getLogger(__name__)
Notify = Callable[[str, str], Awaitable[None]]
DENSE_K = 30
SPARSE_K = 30
CANDIDATES = 30
QUERY_CHARS = 650
RERANK_CHARS = 2000


@dataclass
class Retrieval:
    selected: list[Candidate] = field(default_factory=list)
    chunks: dict[str, dict[str, Any]] = field(default_factory=dict)
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
    await notify("searching", "Ranking source passages")
    dense = store.chunks.dense(vector, DENSE_K, exclude_labels)
    sparse = store.chunks.sparse(plan.terms, SPARSE_K, exclude_labels)
    rankings = {"dense": dense, "sparse": sparse}
    candidates = fuse({k: v for k, v in rankings.items() if v})[: CANDIDATES + 10]
    chunks = {r["id"]: r for r in store.chunks.get_many([c.chunk_id for c in candidates])}
    excluded = set(exclude_labels)
    candidates = [
        c
        for c in candidates
        if c.chunk_id in chunks and not excluded & set(chunks[c.chunk_id]["labels"])
    ][:CANDIDATES]
    if not candidates:
        return Retrieval(trace={"dense": len(dense), "sparse": len(sparse)})
    ids = [c.chunk_id for c in candidates]
    ranked = rerank(
        candidates,
        chunks,
        plan,
        store.chunks.doc_freq(plan.terms),
        store.chunks.count(),
    )
    if ctx.settings.rerank_model:  # a cross-encoder reads question and passage together
        await notify("reranking", "Scoring the candidate passages against your question")
        texts = [
            f"{chunks[c.chunk_id]['context']}\n{chunks[c.chunk_id]['text']}"[:RERANK_CHARS]
            for c in ranked
        ]
        try:
            scores = await ctx.model.relevance(ctx.settings.rerank_model, plan.search_text, texts)
        except (httpx.HTTPError, ValueError, TimeoutError) as exc:  # failing reranker: features
            log.warning(
                "Reranker %s failed (%s); ranking by features", ctx.settings.rerank_model, exc
            )
            scores = []
        weight = ctx.settings.rerank_weight
        for c, p in zip(ranked, scores, strict=False):
            c.features["model"] = p
            c.final = weight * p + (1 - weight) * c.final
        ranked.sort(key=lambda c: (-c.final, c.chunk_id))
        reranked = bool(scores)
    else:
        reranked = False
    selected = select(ranked, store.chunks.vectors(ids), plan.final_k)
    hop = ctx.settings.second_hop
    bridge: list[str] = []
    if hop == "always" or (hop == "relationship" and plan.intent == "relationship"):
        extra_ids, bridge = second_hop(
            store.chunks,
            plan,
            selected,
            {c.chunk_id: chunks[c.chunk_id]["text"] for c in selected},
            {c.chunk_id: chunks[c.chunk_id]["document_id"] for c in selected},
            exclude_labels,
        )
        chunks.update({r["id"]: r for r in store.chunks.get_many(extra_ids)})
        selected += [Candidate(cid, ranks={"hop": n}) for n, cid in enumerate(extra_ids, 1)]
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
        "candidates": len(candidates),
        "reranked": reranked,
        **({"bridge_terms": bridge} if bridge else {}),
        "selected": [
            {
                "chunk_id": c.chunk_id,
                "document": chunks[c.chunk_id]["document_name"],
                "document_id": chunks[c.chunk_id]["document_id"],
                "heading": chunks[c.chunk_id]["heading"],
                "score": round(c.final, 3),
                "ranks": c.ranks,
                **({"completes": "table or list"} if c.chunk_id in added else {}),
            }
            for c in selected
        ],
    }
    return Retrieval(selected, chunks, trace)
