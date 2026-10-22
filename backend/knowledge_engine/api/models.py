from fastapi import APIRouter

from knowledge_engine.config import Settings
from knowledge_engine.context import Ctx
from knowledge_engine.errors import AppError
from knowledge_engine.llm.answer_choice import (
    Provider,
    choose,
    default_answer,
    ollama_answer_models,
    server_answer_models,
)
from knowledge_engine.schemas.models import (
    AnswerModel,
    AnswerModels,
    LoadRequest,
    ModelsStatus,
    ModelState,
    Role,
)

router = APIRouter(tags=["models"])


def configured(s: Settings, answer: str | None = None) -> list[tuple[Role, str]]:
    """The models the engine uses, by role. Only these can be loaded, never a requested name."""
    found: list[tuple[Role, str]] = [
        ("chat", answer or s.chat_model),
        ("embedding", s.embedding_model),
    ]
    if s.rerank_model:
        found.append(("rerank", s.rerank_model))
    if s.context_model and s.contextual_chunks and s.context_model != s.chat_model:
        found.append(("context", s.context_model))
    return found


def names(listed: list[str]) -> set[str]:
    return set(listed) | {m.removesuffix(":latest") for m in listed}  # "x" means "x:latest"


async def server_state(ctx: Ctx, model: str) -> ModelState:
    """The answer model on a model server: asleep, loading (with how long) or ready."""
    found = await ctx.model.chat_state() or {"state": "unreachable"}
    state = {"ready": "loaded", "starting": "loading"}.get(found["state"], found["state"])
    return ModelState(
        role="chat",
        model=model,
        installed=state != "unreachable",
        loaded=state == "loaded",
        state=state,
        where="server",
        detail=found.get("detail"),
        loading_seconds=found.get("loading_seconds"),
    )


async def status_of(
    ctx: Ctx, provider: Provider | None = None, model: str | None = None
) -> ModelsStatus:
    """`provider`/`model`: the answer model the user picked (None: the default one)."""
    provider, model = (provider, model) if provider and model else default_answer(ctx.settings)
    wanted = configured(ctx.settings, model)
    remote = provider == "vllm"
    local = [(r, m) for r, m in wanted if not (remote and r == "chat")]
    chat = await server_state(ctx, model) if remote else None
    try:
        installed = names(await ctx.model.health())
        loaded = {m["name"]: m for m in await ctx.model.loaded()}
    except Exception as exc:
        down = [
            ModelState(role=r, model=m, installed=False, loaded=False, state="unreachable")
            for r, m in local
        ]
        return ModelsStatus(
            ollama_available=False,
            models=([chat] if chat else []) + down,
            error=f"Ollama unavailable: {exc}",
        )
    held = {k.removesuffix(":latest"): v for k, v in loaded.items()} | loaded
    states = [
        ModelState(
            role=role,
            model=model,
            installed=model in installed,
            loaded=model in held,
            state="not_installed"
            if model not in installed
            else "loaded"
            if model in held
            else "not_loaded",
            size_bytes=held[model]["size"] if model in held else None,
            expires_at=held[model]["expires_at"] if model in held else None,
        )
        for role, model in local
    ]
    return ModelsStatus(ollama_available=True, models=([chat] if chat else []) + states)


@router.get("/models/status")
async def models_status(
    ctx: Ctx, answer_provider: Provider | None = None, answer_model: str | None = None
) -> ModelsStatus:
    """Which of the models the engine needs are installed and which are in memory right now
    (with `answer_provider` and `answer_model`: for that answer model)."""
    return await status_of(ctx, answer_provider, answer_model)


@router.get("/models/answer")
async def answer_models(ctx: Ctx) -> AnswerModels:
    """Every model that can write answers: the model server's and Ollama's. A backend that cannot
    be reached is reported in `errors` and the other one is still listed."""
    s = ctx.settings
    default = default_answer(s)
    listed: list[AnswerModel] = []
    errors: list[str] = []
    if s.chat_url:
        try:
            state = await ctx.model.chat_state() or {"state": "unreachable"}
            st = {"ready": "loaded", "starting": "loading"}.get(state["state"], state["state"])
            for m in await server_answer_models(ctx):
                listed.append(
                    AnswerModel(
                        provider="vllm",
                        model=m,
                        default=default == ("vllm", m),
                        state=st if m == s.chat_model else "loaded",
                    )
                )
        except Exception as exc:
            errors.append(f"Model server unreachable: {exc}")
    try:
        held = names([m["name"] for m in await ctx.model.loaded()])
        for m in ollama_answer_models(s, await ctx.model.health()):
            listed.append(
                AnswerModel(
                    provider="ollama",
                    model=m,
                    default=default == ("ollama", m),
                    state="loaded" if m in held else "not_loaded",
                )
            )
    except Exception as exc:
        errors.append(f"Ollama unavailable: {exc}")
    return AnswerModels(models=listed, errors=errors)


@router.post("/models/load")
async def load_models(request: LoadRequest, ctx: Ctx) -> ModelsStatus:
    """Bring the requested models (default: all configured) into memory, one at a time. A model
    that is not installed is never downloaded here: installing stays an explicit `ollama pull`."""
    await choose(ctx, request.answer_provider, request.answer_model)
    before = await status_of(ctx, request.answer_provider, request.answer_model)
    if not before.ollama_available:
        raise AppError(503, before.error or "Ollama unavailable")
    roles = request.roles or [m.role for m in before.models]
    for state in before.models:
        if (
            state.role not in roles
            or state.loaded
            or state.state == "loading"
            and state.role != "chat"
        ):
            continue
        if state.state == "unreachable":
            raise AppError(503, state.detail or f"Cannot reach the server for {state.model}")
        if not state.installed:
            raise AppError(
                409,
                f"Model {state.model} is not installed; run `ollama pull {state.model}`",
            )
        try:
            await ctx.model.load(state.model, embedding=state.role == "embedding")
        except TimeoutError:
            raise AppError(
                504, f"Loading {state.model} took longer than the model timeout"
            ) from None
        except Exception as exc:
            raise AppError(502, f"Could not load {state.model}: {exc}") from exc
    return await status_of(ctx, request.answer_provider, request.answer_model)
