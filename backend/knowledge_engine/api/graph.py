from fastapi import APIRouter, Query

from knowledge_engine.context import Ctx
from knowledge_engine.pipeline.graph_view import knowledge_graph
from knowledge_engine.schemas.graph import Citation, KnowledgeGraph

router = APIRouter(tags=["graph"])


@router.get("/graph")
async def graph(
    ctx: Ctx, min_confidence: float = Query(default=0.0, ge=0.0, le=1.0)
) -> KnowledgeGraph:
    """Every entity and relation; hide weakly-supported relations with min_confidence."""
    return KnowledgeGraph.model_validate(knowledge_graph(ctx, min_confidence))


@router.get("/sources/{chunk_id}")
async def source(chunk_id: str, ctx: Ctx) -> Citation:
    return Citation.model_validate(ctx.store.chunks.citation(chunk_id))
