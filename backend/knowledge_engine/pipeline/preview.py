"""Live preview of a question as it is typed: the entities it names and the relations bearing on it.

No model is called (neither embedding nor generation): entity linking is names, aliases and
typo-tolerant trigram lookups in the database, and a relation counts when both ends are named or its
predicate relates to the other words of the text, the same gate the answer pipeline applies.
"""

from typing import Any

from knowledge_engine.context import AppContext
from knowledge_engine.retrieval.analyzer import correct_spelling, link_entities
from knowledge_engine.retrieval.graph import related
from knowledge_engine.text import STOPWORDS, ordered_terms, token_forms

MAX_RELATIONS = 100


def preview(ctx: AppContext, text: str) -> dict[str, Any]:
    # fix misspellings the way a question is fixed when sent, but leave the word still being typed
    head, space, tail = text.rpartition(" ") if not text[-1:].isspace() else (text, "", "")
    text = correct_spelling(ctx.store, head)[0] + space + tail
    entities = link_entities(ctx.store.graph, ctx.ontology, text, ctx.store.chunks)
    if not entities:
        return {"entities": [], "highlighted_node_ids": [], "highlighted_edge_ids": []}
    named = {e.id for e in entities}
    own = {f for e in entities for f in token_forms(e.name)}
    asked = {f for t in ordered_terms(text) for f in token_forms(t) if f not in STOPWORDS} - own
    edges = [
        r
        for r in ctx.store.graph.relations(sorted(named), 0.0, MAX_RELATIONS)
        if (r["subject_id"] in named and r["object_id"] in named)
        or related(asked, ctx.ontology.keywords(r["predicate"]))
    ]
    nodes = named | {r["subject_id"] for r in edges} | {r["object_id"] for r in edges}
    return {
        "entities": [{"id": e.id, "name": e.name, "type": e.type} for e in entities],
        "highlighted_node_ids": sorted(nodes),
        "highlighted_edge_ids": sorted(r["id"] for r in edges),
    }
