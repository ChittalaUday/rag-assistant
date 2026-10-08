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
    for expected in [s.chat_model, s.embedding_model]:
        if expected not in models:
            warnings.append(f"Model missing: {expected}; install it explicitly with ollama pull")
    docs = ctx.store.documents.stats(PIPELINE_VERSION, s.embedding_model)
    graph = ctx.store.graph.stats()
    index = IndexStats(
        documents=docs.get("documents", 0),
        stale_documents=docs.get("stale", 0),
        chunks=ctx.store.chunks.count(),
        entities=graph.get("entities", 0),
        relations=graph.get("relations", 0),
    )
    if index.stale_documents:
        warnings.append(
            f"{index.stale_documents} document(s) were indexed by an older pipeline or another "
            "embedding model; run `python -m knowledge_engine reindex` to rebuild them"
        )
    stale = {
        v: n for v, n in ctx.store.graph.extractor_versions().items() if v != ctx.extractor.version
    }
    if stale:
        warnings.append(
            f"{sum(stale.values())} piece(s) of relation evidence were extracted with a different "
            f"model, ontology or prompt ({', '.join(sorted(stale))}); run "
            "`python -m knowledge_engine reindex --all` to rebuild them with the current one"
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
