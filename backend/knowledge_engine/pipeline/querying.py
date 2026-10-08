"""Query pipeline: understand -> retrieve -> reason -> generate -> validate -> persist."""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from knowledge_engine.context import AppContext
from knowledge_engine.errors import AppError
from knowledge_engine.generation.answer import generate_answer
from knowledge_engine.pipeline.graph_view import answer_graph
from knowledge_engine.reasoning.evidence import build_evidence
from knowledge_engine.retrieval.analyzer import analyze
from knowledge_engine.retrieval.search import retrieve
from knowledge_engine.schemas.chat import Question

Progress = Callable[[str, str], Awaitable[None]]
HISTORY_MESSAGES = 6


async def chat(ctx: AppContext, q: Question, progress: Progress | None = None) -> dict[str, Any]:
    async def notify(stage: str, message: str) -> None:
        if progress:
            await progress(stage, message)

    await notify("retrieving", "Finding source documents for your question")
    cid = q.conversation_id or str(uuid.uuid4())
    full_history = ctx.store.conversations.messages(cid) if q.conversation_id else []
    history = [
        {"role": m["role"], "content": m["content"]} for m in full_history[-HISTORY_MESSAGES:]
    ]
    try:
        plan = await analyze(ctx, q.question, history, notify)
        retrieval = await retrieve(ctx, plan, q.exclude_labels, notify)
        evidence = build_evidence(ctx, plan, retrieval)
        result = await generate_answer(ctx, plan, history, evidence, notify)
    except AppError:
        raise
    except Exception as exc:
        raise AppError(503, f"Local model failed: {exc}") from exc
    graph = answer_graph(ctx, result.relation_ids)
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
                # ids only: stored snapshots would otherwise grow with the corpus and could
                # reopen passages that were later removed
                "graph": {
                    "highlighted_node_ids": graph["highlighted_node_ids"],
                    "highlighted_edge_ids": graph["highlighted_edge_ids"],
                },
                "warnings": result.warnings,
                "status": result.status,
            },
        ],
    )
    return {
        "conversation_id": cid,
        "answer": result.text,
        "status": result.status,
        "citations": result.citations,
        "graph": graph,
        "warnings": result.warnings,
        "model": ctx.settings.chat_model,
        "plan": {
            "intent": plan.intent,
            "search_text": plan.search_text,
            "rewritten": plan.rewritten,
            "corrections": plan.corrections,
            "entities": [{"id": e.id, "name": e.name, "type": e.type} for e in plan.entities],
            "retrieval": retrieval.trace,
            "facts": len(evidence.facts),
            "conflicts": [
                {"subject": c.subject, "predicate": c.predicate, "objects": list(c.objects)}
                for c in evidence.conflicts
            ],
            "connections": evidence.connections,
        },
    }
