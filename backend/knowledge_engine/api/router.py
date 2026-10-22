from fastapi import APIRouter

from knowledge_engine.api import (
    chat,
    conversations,
    documents,
    health,
    models,
    preview,
    search,
)
from knowledge_engine.schemas.common import ErrorResponse

api_router = APIRouter(prefix="/api", responses={"default": {"model": ErrorResponse}})
for module in (health, models, documents, preview, search, conversations, chat):
    api_router.include_router(module.router)
