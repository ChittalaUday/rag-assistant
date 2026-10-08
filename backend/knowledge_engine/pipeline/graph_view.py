"""The knowledge graph as the API presents it: entities as nodes, relations as edges, each edge
pointing at the verbatim evidence it rests on.

Payloads must not grow with the passages: edges carry a reference and the quote, never passage
text (GET /sources/{chunk_id} serves that), and answers carry only the relations they rest on.
"""

from typing import Any

from knowledge_engine.context import AppContext


def build_edges(ctx: AppContext, relations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """API edges for relations, each with up to five pieces of evidence (reference + quote)."""
    store = ctx.store
    evidence = store.graph.evidence([r["id"] for r in relations], per_relation=5)
    refs = store.chunks.refs(sorted({e["chunk_id"] for e in evidence}))
    by_relation: dict[str, list[dict[str, Any]]] = {}
    for e in evidence:
        if e["chunk_id"] in refs:  # a passage removed since the evidence query is skipped
            by_relation.setdefault(e["relation_id"], []).append(
                {**refs[e["chunk_id"]], "quote": e["quote"]}
            )
    labels = {name: p.label for name, p in ctx.ontology.predicates.items()}
    return [
        {
            "id": r["id"],
            "source": r["subject_id"],
            "target": r["object_id"],
            "label": labels.get(r["predicate"], r["predicate"]),
            "predicate": r["predicate"],
            "confidence": round(float(r["confidence"]), 3),
            "evidence_count": r["evidence_count"],
            "evidence": by_relation.get(r["id"], []),
        }
        for r in relations
    ]


def knowledge_graph(ctx: AppContext, min_confidence: float = 0.0) -> dict[str, Any]:
    """Every entity and relation (nothing highlighted)."""
    return {
        "nodes": [dict(n) for n in ctx.store.graph.nodes()],
        "edges": build_edges(ctx, ctx.store.graph.relations(None, min_confidence)),
        "highlighted_node_ids": [],
        "highlighted_edge_ids": [],
    }


def answer_graph(ctx: AppContext, relation_ids: set[str]) -> dict[str, Any]:
    """Only the relations an answer rests on and their endpoints, all highlighted."""
    if not relation_ids:
        return {"nodes": [], "edges": [], "highlighted_node_ids": [], "highlighted_edge_ids": []}
    edges = build_edges(ctx, ctx.store.graph.relations(relation_ids=sorted(relation_ids)))
    node_ids = sorted({e["source"] for e in edges} | {e["target"] for e in edges})
    return {
        "nodes": [dict(n) for n in ctx.store.graph.nodes(node_ids)],
        "edges": edges,
        "highlighted_node_ids": node_ids,
        "highlighted_edge_ids": sorted(e["id"] for e in edges),
    }
