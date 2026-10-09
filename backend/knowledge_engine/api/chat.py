import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from knowledge_engine.context import Ctx
from knowledge_engine.errors import AppError
from knowledge_engine.pipeline.querying import chat
from knowledge_engine.schemas.chat import ChatResponse, Question
from knowledge_engine.text import ANSWER_LANGUAGES

router = APIRouter(tags=["chat"])


@router.get("/languages")
async def languages() -> dict[str, list[str]]:
    """Languages an answer can be asked for (`answer_language`); without one, answers follow
    the language of the question."""
    return {"languages": list(ANSWER_LANGUAGES)}


@router.post("/chat")
async def chat_endpoint(q: Question, ctx: Ctx) -> ChatResponse:
    return ChatResponse.model_validate(await chat(ctx, q))


def sse(kind: str, data: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(data)}\n\n"


@router.post("/chat/stream", response_class=StreamingResponse)
async def chat_stream(q: Question, ctx: Ctx) -> StreamingResponse:
    """SSE events: status {stage,message}, trace (AnswerTrace so far, sent after understanding and
    after retrieval), draft {text} (the model's answer so far as it is generated, NOT validated:
    replaced by the result), token {text}, result (ChatResponse), error {message}."""

    async def events() -> AsyncIterator[str]:
        updates: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        async def progress(stage: str, message: str) -> None:
            await updates.put(("status", {"stage": stage, "message": message}))

        async def trace(partial: dict[str, Any]) -> None:
            await updates.put(("trace", partial))

        async def draft(text: str) -> None:
            await updates.put(("draft", {"text": text}))

        async def run() -> None:
            try:
                result = await chat(ctx, q, progress, trace, draft)
                await progress("streaming", "Displaying the validated answer")
                await updates.put(("result", result))
            except Exception as exc:
                message = exc.detail if isinstance(exc, AppError) else str(exc)
                await updates.put(("error", {"message": message}))

        task = asyncio.create_task(run())
        try:
            while True:
                kind, data = await updates.get()
                if kind == "result":
                    for start in range(0, len(data["answer"]), 80):
                        yield sse("token", {"text": data["answer"][start : start + 80]})
                    yield sse(kind, data)
                    break
                yield sse(kind, data)
                if kind == "error":
                    break
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
