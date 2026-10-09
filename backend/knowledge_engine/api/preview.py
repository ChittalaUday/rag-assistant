from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from knowledge_engine.context import Ctx
from knowledge_engine.pipeline.preview import preview
from knowledge_engine.schemas.sources import Preview

router = APIRouter(tags=["preview"])


class PreviewRequest(BaseModel):
    text: str = Field(max_length=2000)
    exclude_labels: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("text")
    @classmethod
    def searchable(cls, value: str) -> str:
        if "\x00" in value:  # PostgreSQL text cannot hold NUL
            raise ValueError("must not contain NUL characters")
        return value


@router.post("/preview")
async def preview_question(body: PreviewRequest, ctx: Ctx) -> Preview:
    """Passages the text is likely to draw on, shown while a question is typed (no model call)."""
    return Preview.model_validate(preview(ctx, body.text, body.exclude_labels))
