from fastapi import APIRouter

from knowledge_engine.context import Ctx
from knowledge_engine.pipeline.indexing import PIPELINE_VERSION
from knowledge_engine.schemas.health import Health, IndexStats

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(ctx: Ctx) -> Health:
    s = ctx.settings
    warnings = []
    try:
        models = await ctx.model.health()
    except Exception as exc:
        models = []
        warnings.append(f"Ollama unavailable: {exc}")
    installed = set(models) | {m.removesuffix(":latest") for m in models}  # "x" means "x:latest"
    for expected in [s.chat_model, s.embedding_model, s.rerank_model, s.context_model]:
        if expected and expected not in installed:
            warnings.append(f"Model missing: {expected}; install it explicitly with ollama pull")
    docs = ctx.store.documents.stats(PIPELINE_VERSION, s.embedding_model)
    index = IndexStats(
        documents=docs.get("documents", 0),
        stale_documents=docs.get("stale", 0),
        chunks=ctx.store.chunks.count(),
    )
    if index.stale_documents:
        warnings.append(
            f"{index.stale_documents} document(s) were indexed by an older pipeline or another "
            "embedding model; run `python -m knowledge_engine reindex` to rebuild them"
        )
    other = [m for m in ctx.store.chunks.embedding_models() if m != s.embedding_model]
    if other:
        warnings.append(
            f"Some chunks were embedded with {', '.join(other)}, not {s.embedding_model}; "
            "re-index them before relying on search"
        )
    return Health(
        status="ok",
        ollama_available=bool(models),
        models=models,
        chat_model=s.chat_model,
        embedding_model=s.embedding_model,
        warnings=warnings,
        index=index,
    )
