"""Query pipeline: understand -> retrieve -> reason -> generate -> validate -> persist."""

import re
import subprocess
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import cache
from typing import Any

from knowledge_engine.config import REPO_ROOT
from knowledge_engine.context import AppContext
from knowledge_engine.errors import AppError
from knowledge_engine.generation.answer import Draft, Generated, generate_answer
from knowledge_engine.generation.smalltalk import small_talk_reply
from knowledge_engine.llm.answer_choice import CHOSEN, choose
from knowledge_engine.llm.ollama import USAGE
from knowledge_engine.pipeline.indexing import PIPELINE_VERSION
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


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=5, check=True
    ).stdout.strip()


@cache
def code_version() -> str | None:
    """The git commit the engine runs ("+dirty" with uncommitted changes); None outside git."""
    try:
        return _git("rev-parse", "--short", "HEAD") + (
            "+dirty" if _git("status", "--porcelain") else ""
        )
    except (OSError, subprocess.SubprocessError):
        return None


async def chat(
    ctx: AppContext,
    q: Question,
    progress: Progress | None = None,
    on_trace: TraceSink | None = None,
    on_draft: Draft | None = None,
) -> dict[str, Any]:
    """Answer a question. `progress` receives each stage as it starts; `on_trace` receives the
    trace as it grows (after understanding, after retrieval) so a client can show it live;
    `on_draft` receives the model's answer text as it is generated, before it is validated.
    The saved answer carries `metrics` for later evaluation (see the end of this function)."""
    asked_at = datetime.now(UTC).isoformat(timespec="seconds")
    started = time.perf_counter()
    marks: dict[str, float] = {}
    usage: dict[str, dict[str, int]] = {}
    usage_token = USAGE.set(usage)  # Ollama calls made for this question are counted here
    first_draft: list[float] = []

    async def draft(text: str) -> None:
        if not first_draft:
            first_draft.append(time.perf_counter())
        if on_draft:
            await on_draft(text)

    async def notify(stage: str, message: str) -> None:
        marks.setdefault(stage, time.perf_counter())
        if progress:
            await progress(stage, message)

    await choose(ctx, q.answer_provider, q.answer_model)
    await notify("routing", "Reading your message")
    cid = q.conversation_id or str(uuid.uuid4())
    full_history = ctx.store.conversations.messages(cid) if q.conversation_id else []
    history = [
        {"role": m["role"], "content": m["content"]} for m in full_history[-HISTORY_MESSAGES:]
    ]
    try:
        reply, route_score = await small_talk_reply(
            ctx, q.question, history, q.answer_language or "", notify
        )
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
            result = await generate_answer(
                ctx, plan, history, evidence, notify, draft if on_draft else None
            )
    except AppError:
        raise
    except Exception as exc:
        raise AppError(503, f"Local model failed: {exc}") from exc
    finally:
        USAGE.reset(usage_token)
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
    s = ctx.settings
    # for evaluations: what produced this answer and how (timings are in trace.timings)
    metrics = {
        "asked_at": asked_at,
        "route": "small_talk" if result.status == "conversation" else "documents",
        "route_score": None if route_score is None else round(route_score, 4),
        "first_draft_ms": _ms(first_draft[0] - started) if first_draft else None,
        # each stage from its start to the next stage's (the last one to the end); a stage that
        # runs twice (waiting_for_model) is timed from its first start
        "stages_ms": stage_durations(marks, started, done),
        "retries": result.retries,
        "statements": {
            "total": len(result.statements),
            "supported": sum(1 for st in result.statements if st["sources"]),
        },
        "request": {"answer_language": q.answer_language, "exclude_labels": q.exclude_labels},
        "models": {
            "chat": (CHOSEN.get() or ("", s.chat_model))[1],
            "embedding": s.embedding_model,
            "rerank": s.rerank_model or None,
        },
        "settings": {
            "answer_thinking": s.answer_thinking,
            "verify_citations": s.verify_citations,
            "second_hop": s.second_hop,
            "contextual_chunks": s.contextual_chunks,
            "chunker": s.chunker,
            "context_chars": s.context_chars,
            "small_talk_margin": s.small_talk_margin,
        },
        "usage": usage,
        "engine": {"pipeline_version": PIPELINE_VERSION, "code": code_version()},
    }
    ctx.store.conversations.save_exchange(
        cid,
        new=not q.conversation_id,
        title=q.question,
        messages=[
            {"role": "user", "content": q.question, "asked_at": asked_at},
            {
                "role": "assistant",
                "content": result.text,
                "citations": result.citations,
                "warnings": result.warnings,
                "status": result.status,
                "statements": result.statements,
                "trace": trace,
                "metrics": metrics,
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
        "metrics": metrics,
        "model": (CHOSEN.get() or ("", ctx.settings.chat_model))[1],
    }


def stage_durations(marks: dict[str, float], started: float, done: float) -> dict[str, int]:
    """{stage: ms} in order: from each stage's start to the next one's, the last one to `done`;
    time before the first mark is "setup"."""
    order = sorted(marks.items(), key=lambda kv: kv[1])
    ends = [t for _, t in order[1:]] + [done]
    out = {"setup": _ms(order[0][1] - started)} if order else {}
    out.update({stage: _ms(end - start) for (stage, start), end in zip(order, ends, strict=True)})
    return out


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
