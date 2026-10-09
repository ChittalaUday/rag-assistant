from fastapi import APIRouter

from knowledge_engine.context import Ctx
from knowledge_engine.schemas.chat import ConversationDetail, ConversationList
from knowledge_engine.schemas.common import OkResponse

router = APIRouter(tags=["conversations"])


@router.get("/conversations")
async def list_conversations(ctx: Ctx) -> ConversationList:
    return ConversationList.model_validate({"conversations": ctx.store.conversations.all()})


# exclude_unset: user messages carry no citations/warnings/trace keys.
@router.get("/conversations/{id}", response_model_exclude_unset=True)
async def conversation(id: str, ctx: Ctx) -> ConversationDetail:
    return ConversationDetail.model_validate(
        {"id": id, "messages": ctx.store.conversations.messages(id)}
    )


@router.delete("/conversations/{id}")
async def delete_conversation(id: str, ctx: Ctx) -> OkResponse:
    ctx.store.conversations.delete(id)
    return OkResponse(ok=True)
