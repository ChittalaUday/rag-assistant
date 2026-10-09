"""Small-talk routing: how fixture questions and held-out small talk score against the examples in
generation/smalltalk.toml, and which KE_SMALL_TALK_MARGIN separates them. Needs only the embedding
model (no database, no chat model)."""

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from knowledge_engine.config import Settings
from knowledge_engine.generation.smalltalk import route_score
from knowledge_engine.llm.gateway import ModelGateway
from knowledge_engine.llm.ollama import Ollama

MARGINS = (0.0, 0.05, 0.08, 0.1, 0.11, 0.12, 0.15)


async def evaluate_routing(
    settings: Settings, question_files: list[Path], held_out: Path
) -> dict[str, Any]:
    ctx: Any = SimpleNamespace(
        model=ModelGateway(Ollama(settings), settings.model_timeout), settings=settings
    )
    data = json.loads(held_out.read_text(encoding="utf-8"))
    questions = [q["question"] for f in question_files for q in json.loads(f.read_text())]
    questions += data["questions"]
    await route_score(ctx, "warm up")  # embeds the examples once
    start = time.perf_counter()
    q = sorted([(await route_score(ctx, m), m) for m in questions], reverse=True)
    per_ms = (time.perf_counter() - start) / len(questions) * 1000
    talk = sorted([(await route_score(ctx, m), m) for m in data["small_talk"]])
    highest_question, lowest_caught = (
        q[0][0],
        min((s for s, _ in talk if s > q[0][0]), default=None),
    )
    return {
        "embedding_model": settings.embedding_model,
        "ms_per_message": round(per_ms, 1),
        "questions": len(q),
        "small_talk": len(talk),
        "highest_question_scores": [(round(s, 3), m) for s, m in q[:5]],
        "lowest_small_talk_scores": [(round(s, 3), m) for s, m in talk[:5]],
        "margins": {
            f"{m:.2f}": {
                "small_talk_caught": sum(s >= m for s, _ in talk),
                "questions_misrouted": sum(s >= m for s, _ in q),
            }
            for m in MARGINS
        },
        # between the highest question and the lowest small talk above it
        "suggested_margin": round((highest_question + lowest_caught) / 2, 3)
        if lowest_caught
        else None,
        "current_margin": settings.small_talk_margin,
    }
