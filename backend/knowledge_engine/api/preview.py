from fastapi import APIRouter
from pydantic import BaseModel, Field

from knowledge_engine.context import Ctx
from knowledge_engine.pipeline.preview import preview
from knowledge_engine.schemas.graph import Preview

router = APIRouter(tags=["preview"])


class PreviewRequest(BaseModel):
    text: str = Field(max_length=2000)


@router.post("/preview")
async def preview_question(body: PreviewRequest, ctx: Ctx) -> Preview:
    """Entities and relations a question mentions, to highlight as it is typed (no model call)."""
    return Preview.model_validate(preview(ctx, body.text))
