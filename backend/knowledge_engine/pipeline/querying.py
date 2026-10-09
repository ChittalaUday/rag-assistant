"""Query pipeline: understand -> retrieve -> reason -> generate -> validate -> persist."""

import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from knowledge_engine.context import AppContext
from knowledge_engine.errors import AppError
from knowledge_engine.generation.answer import Draft, Generated, generate_answer
from knowledge_engine.generation.smalltalk import small_talk_reply
from knowledge_engine.reasoning.evidence import build_evidence
from knowledge_engine.retrieval.analyzer import analyze
from knowledge_engine.retrieval.models import QueryPlan
from knowledge_engine.retrieval.search import Retrieval, retrieve
from knowledge_engine.schemas.chat import Question

Progress = Callable[[str, str], Awaitable[None]]
TraceSink = Callable[[dict[str, Any]], Awaitable[None]]
HISTORY_MESSAGES = 6


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


async def chat(
    ctx: AppContext,
    q: Question,
    progress: Progress | None = None,
    on_trace: TraceSink | None = None,
    on_draft: Draft | None = None,
) -> dict[str, Any]:
    """Answer a question. `progress` receives each stage as it starts; `on_trace` receives the
    trace as it grows (after understanding, after retrieval) so a client can show it live;
    `on_draft` receives the model's answer text as it is generated, before it is validated."""
    started = time.perf_counter()
    marks: dict[str, float] = {}

    async def notify(stage: str, message: str) -> None:
        marks.setdefault(stage, time.perf_counter())
        if progress:
            await progress(stage, message)

    await notify("reading", "Reading your message")
    cid = q.conversation_id or str(uuid.uuid4())
    full_history = ctx.store.conversations.messages(cid) if q.conversation_id else []
    history = [
        {"role": m["role"], "content": m["content"]} for m in full_history[-HISTORY_MESSAGES:]
    ]
    try:
        reply = await small_talk_reply(ctx, q.question, history, q.answer_language or "")
        if reply is not None:  # small talk: answered without retrieval, states no facts
            plan = QueryPlan(q.question, q.question, False, [], "conversation", 0)
            retrieval, shown = Retrieval(), set()
            understood = found = time.perf_counter()
            result = Generated(text=reply, status="conversation")
        else:
            await notify("retrieving", "Finding source documents for your question")
            plan = await analyze(ctx, q.question, history, notify)
            plan.answer_language = q.answer_language or ""
            understood = time.perf_counter()
            if on_trace:
                await on_trace(build_trace(plan, Retrieval(), set()))
            retrieval = await retrieve(ctx, plan, q.exclude_labels, notify)
            evidence = build_evidence(ctx, retrieval)
            shown = {s.chunk_id for s in evidence.sources}
            found = time.perf_counter()
            if on_trace:
                await on_trace(build_trace(plan, retrieval, shown))
            result = await generate_answer(ctx, plan, history, evidence, notify, on_draft)
    except AppError:
        raise
    except Exception as exc:
        raise AppError(503, f"Local model failed: {exc}") from exc
    done = time.perf_counter()
    checked = marks.get("verifying", done)
    trace = build_trace(plan, retrieval, shown)
    trace["timings"] = {
        "understand_ms": _ms(understood - started),
        "retrieve_ms": _ms(found - understood),
        "answer_ms": _ms(checked - found),
        "check_ms": _ms(done - checked),
        "total_ms": _ms(done - started),
    }
    ctx.store.conversations.save_exchange(
        cid,
        new=not q.conversation_id,
        title=q.question,
        messages=[
            {"role": "user", "content": q.question},
            {
                "role": "assistant",
                "content": result.text,
                "citations": result.citations,
                "warnings": result.warnings,
                "status": result.status,
                "statements": result.statements,
                "trace": trace,
            },
        ],
    )
    return {
        "conversation_id": cid,
        "answer": result.text,
        "status": result.status,
        "citations": result.citations,
        "warnings": result.warnings,
        "statements": result.statements,
        "trace": trace,
        "model": ctx.settings.chat_model,
    }


def build_trace(plan: QueryPlan, retrieval: Retrieval, shown: set[str]) -> dict[str, Any]:
    """How the answer was produced, in the shape of schemas.chat.AnswerTrace. Only passages the
    answer model was actually shown are listed (the evidence budget can leave some out)."""
    t = retrieval.trace
    return {
        "search_text": plan.search_text,
        "rewritten": plan.rewritten,
        "corrections": plan.corrections,
        "intent": plan.intent,
        "counts": {
            "dense": t.get("dense", 0),
            "sparse": t.get("sparse", 0),
            "candidates": t.get("candidates", 0),
        },
        "reranked": t.get("reranked", False),
        "bridge_terms": t.get("bridge_terms", []),
        "passages": [
            {
                "chunk_id": p["chunk_id"],
                "document_id": p["document_id"],
                "document": p["document"],
                "heading": p["heading"],
                "snippet": snippet(retrieval.chunks[p["chunk_id"]]["text"]),
                "score": p["score"],
                "ranks": p["ranks"],
                "completes": p.get("completes"),
            }
            for p in t.get("selected", [])
            if p["chunk_id"] in shown
        ],
    }


def snippet(text: str, limit: int = 140) -> str:
    """The start of a passage as one line, without markdown heading lines (shown in the trace)."""
    body = " ".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    body = re.sub(r"\s+", " ", body).strip()
    return body if len(body) <= limit else body[: limit - 1].rsplit(" ", 1)[0] + "…"
