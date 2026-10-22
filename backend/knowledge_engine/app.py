import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress

import psycopg
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from knowledge_engine.api.router import api_router
from knowledge_engine.config import Settings
from knowledge_engine.context import AppContext
from knowledge_engine.errors import AppError
from knowledge_engine.llm.gateway import ModelGateway
from knowledge_engine.llm.ollama import ModelClient, Ollama
from knowledge_engine.llm.server import Routed, ServerChat
from knowledge_engine.pipeline.indexing import worker
from knowledge_engine.store import Store
from knowledge_engine.store.migrate import align_embedding_dimensions, upgrade

log = logging.getLogger(__name__)


def create_context(settings: Settings, model_client: ModelClient | None = None) -> AppContext:
    """Everything the pipelines need except the database connection (opened by open_store)."""
    client: ModelClient = model_client or Ollama(settings)
    if settings.chat_url and model_client is None:
        client = Routed(client, ServerChat(settings), settings)  # answers come from the server
    return AppContext(
        settings=settings,
        model=ModelGateway(client, settings.model_timeout, settings.model_load_timeout),
        queue=asyncio.Queue(maxsize=settings.max_jobs),
    )


def open_store(ctx: AppContext) -> None:
    """Migrate (unless disabled), connect, and build the components that need the database."""
    s = ctx.settings
    s.data_dir.mkdir(parents=True, exist_ok=True)
    if s.auto_migrate:
        upgrade(s.database_url, "head", s.embedding_dimensions, s.embedding_model)
        if align_embedding_dimensions(s.database_url, s.embedding_dimensions):
            log.warning(
                "Embedding size changed to %d: documents will be re-embedded",
                s.embedding_dimensions,
            )
    ctx.store = Store(s.database_url)


def create_app(
    settings: Settings | None = None, model_client: ModelClient | None = None
) -> FastAPI:
    ctx = create_context(settings or Settings(), model_client)

    async def refill(ids: list[str]) -> None:
        """Re-enqueue documents left queued by a restart without blocking startup: the bounded
        queue admits them as the worker frees slots."""
        for id in ids:
            ctx.store.documents.progress(id, "queued", "Queued after restart")
            await ctx.queue.put(id)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        open_store(ctx)
        docs = ctx.store.documents
        for id in docs.reembedding_needed(ctx.settings.embedding_model):  # model or size changed
            docs.requeue(id)
        tasks = [
            asyncio.create_task(worker(ctx)),
            asyncio.create_task(refill(docs.requeue_interrupted())),
        ]
        yield
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        ctx.store.close()

    app = FastAPI(title="Knowledge Engine", lifespan=lifespan)
    app.state.ctx = ctx
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ctx.settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def refuse_cross_site_writes(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """A web page can POST multipart forms to localhost without a CORS preflight; refuse
        writes whose Origin is neither an allowed UI origin nor this server itself."""
        origin = request.headers.get("origin")
        if (
            origin
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and origin not in ctx.settings.cors_origins
            and origin != f"{request.url.scheme}://{request.headers.get('host', '')}"
        ):
            return JSONResponse(
                {"detail": f"Origin {origin} is not allowed; add it to KE_CORS_ORIGINS"},
                status_code=403,
            )
        return await call_next(request)

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ctx.settings.allowed_hosts)

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(psycopg.DataError)
    @app.exception_handler(UnicodeError)
    async def unstorable_text(_: Request, exc: Exception) -> JSONResponse:
        """NUL bytes and lone surrogates cannot be stored in PostgreSQL text."""
        return JSONResponse({"detail": "Text contains characters that cannot be stored"}, 422)

    app.include_router(api_router)
    return app
