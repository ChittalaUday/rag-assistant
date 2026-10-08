"""Retrieval systems under evaluation, built on the real pipeline in an isolated schema."""

import hashlib
import os
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import psycopg

from knowledge_engine.app import create_context, open_store
from knowledge_engine.config import Settings
from knowledge_engine.context import AppContext
from knowledge_engine.evaluation.dataset import Case
from knowledge_engine.evaluation.metrics import Hit
from knowledge_engine.extraction.extractor import ExtractionCache, Extractor
from knowledge_engine.ingestion.chunker import chunk_document
from knowledge_engine.llm.ollama import Ollama
from knowledge_engine.pipeline.indexing import process
from knowledge_engine.retrieval import fusion
from knowledge_engine.retrieval.analyzer import analyze
from knowledge_engine.retrieval.fusion import fuse
from knowledge_engine.retrieval.search import DENSE_K, SPARSE_K, embed_query, retrieve
from knowledge_engine.text import normalize_text


async def _quiet(stage: str, message: str) -> None:
    return None


def evaluation_database_url(settings: Settings) -> str:
    """Where evaluations build their throw-away index: never the application database.

    KE_EVAL_DATABASE_URL, else KE_TEST_DATABASE_URL, else '<application database>_test'.
    """
    explicit = os.environ.get("KE_EVAL_DATABASE_URL") or os.environ.get("KE_TEST_DATABASE_URL")
    if explicit:
        return explicit.split("?")[0]
    base = settings.database_url.split("?")[0]
    return base if base.endswith("_test") else base + "_test"


def ensure_database(url: str) -> None:
    """Create the evaluation database and extensions if missing; refuse a database that holds
    application tables in `public` (a scratch schema falls back to `public`, so those tables would
    be read and written by mistake)."""
    try:
        conn = psycopg.connect(url, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as exc:
        if "does not exist" not in str(exc):
            raise
        server, _, name = url.rpartition("/")
        with psycopg.connect(server + "/postgres", autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{name}"')
        conn = psycopg.connect(url, autocommit=True)
    with conn:
        if conn.execute("SELECT to_regclass('public.documents')").fetchone()[0] is not None:  # type: ignore[index]
            raise RuntimeError(
                f"{url} has application tables in public; evaluations need a dedicated database "
                "(set KE_EVAL_DATABASE_URL). Refusing to run."
            )
        for ext in ("vector", "pg_trgm"):
            conn.execute(f"CREATE EXTENSION IF NOT EXISTS {ext} WITH SCHEMA public")


@asynccontextmanager
async def scratch_context(
    settings: Settings, cache: ExtractionCache | None = None
) -> AsyncIterator[AppContext]:
    """An app context on a throw-away schema of the evaluation database."""
    base = evaluation_database_url(settings)
    ensure_database(base)
    schema = "eval_" + uuid.uuid4().hex[:10]
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
        with tempfile.TemporaryDirectory() as data_dir:
            url = f"{base}?options={quote(f'-csearch_path={schema},public')}"
            scoped = settings.model_copy(update={"database_url": url, "data_dir": Path(data_dir)})
            ctx = create_context(scoped, None)
            try:
                open_store(ctx)
                if cache is not None:
                    ctx.extractor = Extractor(ctx.model, ctx.ontology, scoped.chat_model, cache)
                yield ctx
            finally:  # also when opening the store failed: never leave a scratch schema behind
                if hasattr(ctx, "store"):
                    ctx.store.close()
                admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _same_database(a: str, b: str) -> bool:
    def norm(url: str) -> str:
        return url.split("?")[0].rstrip("/").replace("localhost", "127.0.0.1")

    return norm(a) == norm(b)


@asynccontextmanager
async def live_context(settings: Settings, url: str) -> AsyncIterator[AppContext]:
    """A context on an existing, already-populated scratch database: no migrations, and every
    transaction is read-only. The application's own database is refused."""
    if _same_database(url, settings.database_url):
        raise RuntimeError(
            f"{url} is the application database (KE_DATABASE_URL); use a scratch copy. Refusing."
        )
    read_only = (
        f"{url}{'&' if '?' in url else '?'}options={quote('-cdefault_transaction_read_only=on')}"
    )
    with tempfile.TemporaryDirectory() as data_dir:
        scoped = settings.model_copy(
            update={"database_url": read_only, "data_dir": Path(data_dir), "auto_migrate": False}
        )
        ctx = create_context(scoped, None)
        open_store(ctx)
        try:
            yield ctx
        finally:
            ctx.store.close()


async def index_corpus(ctx: AppContext, corpus: dict[str, str], extract: bool) -> None:
    for name, text in corpus.items():
        data = text.encode()
        digest = hashlib.sha256(data).hexdigest()
        path = ctx.settings.data_dir / (digest + Path(name).suffix.lower())
        path.write_bytes(data)
        id = str(uuid.uuid4())
        ctx.store.documents.add(id, name, digest, str(path), [])
        await process(ctx, id, extract)
        if ctx.store.documents.get(id)["status"] != "ready":
            raise RuntimeError(f"{name}: {ctx.store.documents.get(id)['error']}")


class PipelineSystem:
    """dense | sparse | hybrid (rank fusion only) | rerank (full retrieval, no graph extracted) |
    graph (full retrieval with the entity graph) | nograph (same index, graph signals off)."""

    def __init__(self, ctx: AppContext, mode: str):
        self.ctx, self.mode = ctx, mode
        self.name = mode

    async def _plan(self, case: Case):  # type: ignore[no-untyped-def]
        history = [
            {"role": role, "content": text}
            for q in case.history
            for role, text in (("user", q), ("assistant", "(answer)"))
        ]
        return await analyze(self.ctx, case.question, history, _quiet)

    async def retrieve(self, case: Case, k: int) -> list[Hit]:
        ctx = self.ctx
        plan = await self._plan(case)
        plan.final_k = k
        if self.mode == "nograph":
            plan.seeds = []  # same ranking, graph signals switched off
        elif self.mode in {"graph", "graphlist", "entityfeature"}:
            plan.seeds = plan.entities  # graph signals always on
        if self.mode in {"rerank", "graph", "nograph", "graphlist", "entityfeature"}:
            # ablations: only the fused graph list, or only the entity-coverage rerank feature
            weights = (fusion.WEIGHTS["graph"], fusion.FEATURE_WEIGHTS["entity"])
            if self.mode == "graphlist":
                fusion.FEATURE_WEIGHTS["entity"] = 0.0
            if self.mode == "entityfeature":
                fusion.WEIGHTS["graph"] = 0.0
            try:
                result = await retrieve(ctx, plan, [], _quiet)
            finally:
                fusion.WEIGHTS["graph"], fusion.FEATURE_WEIGHTS["entity"] = weights
            ids = [c.chunk_id for c in result.selected]
        else:
            vector = await embed_query(ctx, plan.search_text, _quiet)
            dense = ctx.store.chunks.dense(vector, DENSE_K, [])
            sparse = ctx.store.chunks.sparse(plan.terms, SPARSE_K, [])
            rankings = {"dense": dense, "sparse": sparse}
            use = {"dense": ["dense"], "sparse": ["sparse"], "hybrid": ["dense", "sparse"]}[
                self.mode
            ]
            ids = [c.chunk_id for c in fuse({n: rankings[n] for n in use if rankings[n]})][:k]
        return [Hit(r["document_name"], r["text"]) for r in ctx.store.chunks.get_many(ids)]


def legacy_chunk_count(text: str) -> int:
    return len(chunk_document(normalize_text(text)))


__all__ = ["Ollama", "PipelineSystem", "index_corpus", "live_context", "scratch_context"]
