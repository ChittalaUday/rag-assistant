from fastapi import APIRouter

from knowledge_engine.context import Ctx
from knowledge_engine.retrieval.analyzer import analyze
from knowledge_engine.retrieval.search import retrieve
from knowledge_engine.schemas.entities import SearchHit, SearchRequest, SearchResponse

router = APIRouter(tags=["search"])


async def _quiet(stage: str, message: str) -> None:
    return None


@router.post("/search")
async def search(body: SearchRequest, ctx: Ctx) -> SearchResponse:
    """Retrieval only (no answer generation): the ranked passages and how each retriever ranked
    them. Use it to inspect or debug what the answer pipeline would be given."""
    plan = await analyze(ctx, body.query, [], _quiet)
    plan.final_k = body.k
    retrieval = await retrieve(ctx, plan, body.exclude_labels, _quiet)
    hits = [
        SearchHit(
            chunk_id=c.chunk_id,
            document_id=retrieval.chunks[c.chunk_id]["document_id"],
            document_name=retrieval.chunks[c.chunk_id]["document_name"],
            heading=retrieval.chunks[c.chunk_id]["heading"],
            text=retrieval.chunks[c.chunk_id]["text"],
            score=round(c.final, 4),
            ranks=c.ranks,
        )
        for c in retrieval.selected
    ]
    return SearchResponse(
        hits=hits,
        plan={
            "intent": plan.intent,
            "terms": plan.terms,
            "corrections": plan.corrections,
            "entities": [{"id": e.id, "name": e.name, "type": e.type} for e in plan.entities],
            "retrieval": {k: v for k, v in retrieval.trace.items() if k != "selected"},
        },
    )
