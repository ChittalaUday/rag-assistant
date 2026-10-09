from knowledge_engine.retrieval.fusion import fuse, rerank, select
from knowledge_engine.retrieval.models import QueryPlan


def plan(terms):
    return QueryPlan("q", "q", False, list(terms), "lookup", 3)


def row(id, text, context="doc"):
    return {"id": id, "text": text, "context": context}


def test_reciprocal_rank_fusion_rewards_agreement_between_retrievers():
    ranked = fuse(
        {"dense": [("a", 0.9), ("b", 0.8), ("c", 0.1)], "sparse": [("b", 5.0), ("d", 1.0)]}
    )
    assert [c.chunk_id for c in ranked][0] == "b"
    assert ranked[0].ranks == {"dense": 2, "sparse": 1}
    assert {c.chunk_id for c in ranked} == {"a", "b", "c", "d"}


def test_rerank_prefers_chunks_covering_rare_query_terms():
    candidates = fuse({"dense": [("common", 0.9), ("rare", 0.8)]})
    chunks = {
        "common": row("common", "company policy overview"),
        "rare": row("rare", "company policy on salvage tiers"),
    }
    out = rerank(
        candidates,
        chunks,
        plan(["company", "policy", "salvage"]),
        {"company": 50, "policy": 40, "salvage": 1},
        100,
    )
    assert out[0].chunk_id == "rare" and out[0].features["coverage"] > out[1].features["coverage"]


def test_selection_skips_near_duplicates():
    ranked = fuse({"dense": [("a", 0.9), ("a2", 0.89), ("b", 0.5)]})
    for c, f in zip(ranked, (1.0, 0.99, 0.6), strict=True):
        c.final = f
    vectors = {"a": [1.0, 0.0], "a2": [1.0, 0.0], "b": [0.0, 1.0]}
    assert [c.chunk_id for c in select(ranked, vectors, 3)] == ["a", "b"]


def test_selection_keeps_score_order_and_does_not_trade_relevance_for_diversity():
    """Related passages stay in the top k: a similar second source must not be displaced by a
    dissimilar, lower-scoring one (the failure of a diversity penalty on cross-document questions)."""
    ranked = fuse({"dense": [("a", 0.9), ("similar", 0.8), ("far", 0.7), ("last", 0.6)]})
    for c, f in zip(ranked, (1.0, 0.9, 0.5, 0.4), strict=True):
        c.final = f
    vectors = {
        "a": [1.0, 0.0],
        "similar": [0.8, 0.6],  # cosine 0.8 with "a": related, not a duplicate
        "far": [0.0, 1.0],
        "last": [0.6, 0.8],
    }
    assert [c.chunk_id for c in select(ranked, vectors, 2)] == ["a", "similar"]
    assert [c.chunk_id for c in select(ranked, vectors, 10)] == ["a", "similar", "far", "last"]
