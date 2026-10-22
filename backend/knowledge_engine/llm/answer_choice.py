"""Which model writes this question's answer: the server's (vLLM) or one of Ollama's.

The choice travels in a context variable set when a question starts, so the many calls an answer
makes (rewrite, small talk, answer, attribution) need no extra parameter: `ModelGateway.generate`
reads it when the caller names no model. `Routed` then sends the call to the right backend.
"""

from contextvars import ContextVar
from typing import Any, Literal

from knowledge_engine.config import Settings
from knowledge_engine.errors import AppError

Provider = Literal["vllm", "ollama"]
CHOSEN: ContextVar[tuple[Provider, str] | None] = ContextVar("answer_model", default=None)
# ponytail: name heuristic for Ollama's embedding/rerank models, a capability flag if it misfires
NOT_CHAT = ("embed", "rerank", "bge-", "minilm")


def default_answer(s: Settings) -> tuple[Provider, str]:
    return ("vllm" if s.chat_url else "ollama", s.chat_model)


def ollama_answer_models(s: Settings, installed: list[str]) -> list[str]:
    skip = {s.embedding_model, s.rerank_model}
    return [
        m
        for m in installed
        if m.removesuffix(":latest") not in skip and not any(w in m.lower() for w in NOT_CHAT)
    ]


async def server_answer_models(ctx: Any) -> list[str]:
    """The configured server model, plus whatever the server lists while it is awake (asking an
    asleep model server would wake it)."""
    found = [ctx.settings.chat_model]
    state = await ctx.model.chat_state() or {}
    if state.get("state") == "ready":
        for m in await ctx.model.server_models():
            if m not in found:
                found.append(m)
    return found


async def choose(ctx: Any, provider: Provider | None, model: str | None) -> None:
    """Validate the requested answer model against what is installed or served; 422 otherwise."""
    if provider is None or model is None:
        CHOSEN.set(None)
        return
    s: Settings = ctx.settings
    if provider == "vllm":
        if not s.chat_url:
            raise AppError(422, "No model server is configured")
        allowed = await server_answer_models(ctx)
    else:
        try:
            allowed = ollama_answer_models(s, await ctx.model.health())
        except Exception as exc:
            raise AppError(503, f"Ollama unavailable: {exc}") from exc
    if model not in allowed:
        raise AppError(422, f"{model} is not an available {provider} answer model")
    CHOSEN.set((provider, model))
