from fastapi import APIRouter, Query

from knowledge_engine.context import Ctx
from knowledge_engine.errors import AppError
from knowledge_engine.pipeline.graph_view import build_edges
from knowledge_engine.schemas.entities import EntityDetail, EntityList

router = APIRouter(tags=["entities"])


@router.get("/entities")
async def list_entities(
    ctx: Ctx,
    query: str = "",
    type: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> EntityList:
    """Browse or search canonical entities by name or alias; most connected first."""
    return EntityList.model_validate(
        {"entities": ctx.store.graph.search(query, type, limit, offset)}
    )


@router.get("/entities/{id}")
async def entity(id: str, ctx: Ctx) -> EntityDetail:
    found = ctx.store.graph.entity(id)
    if found is None:
        raise AppError(404, "Entity not found")
    relations = ctx.store.graph.relations([id])
    return EntityDetail.model_validate({**found, "relations": build_edges(ctx, relations)})
