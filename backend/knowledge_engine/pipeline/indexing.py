"""Indexing pipeline: documents in, searchable passages out.

    parse -> normalise -> chunk -> situate (optional) -> embed     (slow, no database writes)
          -> write chunks and refresh the vocabulary               (one atomic transaction)

A failure anywhere before the transaction leaves the previously published version untouched.
"""

import asyncio
import math
import re
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from knowledge_engine.context import AppContext
from knowledge_engine.ingestion.chunker import Chunk, chunk_document
from knowledge_engine.ingestion.parser import parse
from knowledge_engine.ingestion.situate import situate
from knowledge_engine.store import ChunkRow
from knowledge_engine.text import normalize_text

PIPELINE_VERSION = 5  # 4: no entity graph; 5: contextual summaries on by default
EMBED_BATCH = 16


def document_title(name: str) -> str:
    return re.sub(r"[\s_\-]+", " ", Path(name).stem).strip() or name


def chunk_context(title: str, chunk: Chunk) -> str:
    """Text prepended to a chunk for embedding and full-text search (not part of the evidence)."""
    parts = [title, chunk.heading, (chunk.table_header or "")[:160] or None]
    return " › ".join(p for p in parts if p)


@dataclass
class Built:
    text: str
    chunks: list[Chunk]
    contexts: list[str]
    vectors: list[list[float]]
    warnings: list[str] = field(default_factory=list)


async def build(ctx: AppContext, id: str, name: str, path: Path) -> Built:
    s, store = ctx.settings, ctx.store
    store.documents.progress(id, "parsing", "Reading and parsing the document")
    text = normalize_text(await parse(path))
    chunks = chunk_document(text, max_chars=s.chunk_chars)
    if not chunks:
        raise ValueError("Document contains no readable text")
    if len(chunks) > s.max_chunks:
        raise ValueError(f"Document exceeds {s.max_chunks} chunks; split it into smaller files")
    title = document_title(name)
    contexts = [chunk_context(title, c) for c in chunks]
    warnings: list[str] = []
    if s.contextual_chunks:
        stage = "Summarising where each passage sits in the document"
        store.documents.progress(id, "situating", stage, 0, len(chunks))
        failed = 0
        for n, chunk in enumerate(chunks):
            try:
                summary = await situate(
                    ctx.model, store.cache, s.context_model or s.chat_model, title, text, chunk
                )
            except ValueError:  # malformed model output: the passage keeps its plain context
                summary, failed = "", failed + 1
            if summary:
                contexts[n] = f"{contexts[n]}\n{summary}"
            store.documents.progress(id, "situating", stage, n + 1, len(chunks))
        if failed:
            warnings.append(
                f"{failed} passage(s) could not be summarised; they stay searchable by their text"
            )

    store.documents.progress(id, "embedding", "Embedding source chunks", 0, len(chunks))
    inputs = [
        f"{s.embedding_document_prefix}{ctx_}\n{c.text}"
        for ctx_, c in zip(contexts, chunks, strict=True)
    ]
    vectors: list[list[float]] = []
    for i in range(0, len(inputs), EMBED_BATCH):
        vectors.extend(await ctx.model.embed(inputs[i : i + EMBED_BATCH]))
        store.documents.progress(
            id, "embedding", "Embedding source chunks", len(vectors), len(chunks)
        )
    if (
        len(vectors) != len(chunks)
        or any(len(v) != s.embedding_dimensions for v in vectors)
        or any(not math.isfinite(x) for v in vectors for x in v)
    ):
        raise ValueError(
            "Embedding model returned invalid vectors "
            f"(expected {s.embedding_dimensions} dimensions)"
        )
    return Built(text, chunks, contexts, vectors, warnings)


def publish(ctx: AppContext, id: str, built: Built) -> int:
    """Atomically replace the document's chunks."""
    store = ctx.store
    rows = [
        ChunkRow(
            id=str(uuid.uuid4()),
            position=c.position,
            text=c.text,
            char_start=c.char_start,
            char_end=c.char_end,
            heading=c.heading,
            context=context,
            embedding=vector,
            embedding_model=ctx.settings.embedding_model,
        )
        for c, context, vector in zip(built.chunks, built.contexts, built.vectors, strict=True)
    ]
    with store.transaction():
        store.documents.save_text(id, built.text)
        store.chunks.replace(id, rows)
        store.chunks.refresh_lexicon()
        store.documents.mark_ready(id, len(rows), built.warnings, PIPELINE_VERSION)
    return len(rows)


def clean_uploads(ctx: AppContext) -> None:
    """Delete stored upload files no document row references any more. Files are matched by
    name (their hash), not by path spelling, so a different working directory or symlink to the
    same data directory never makes a referenced upload look orphaned."""
    referenced = {Path(p).name for p in ctx.store.documents.referenced_paths()}
    for path in ctx.settings.data_dir.iterdir():
        if (
            re.fullmatch(r"[0-9a-f]{64}\.(txt|md|csv|pdf|docx)", path.name)
            and path.name not in referenced
        ):
            with suppress(OSError):  # failing to tidy must not fail a published document
                path.unlink(missing_ok=True)


async def process(ctx: AppContext, id: str) -> None:
    docs = ctx.store.documents
    record = docs.record(id)
    if not record:
        return
    staged = docs.pending(id)
    name = staged["name"] if staged else record["name"]
    path = ctx.upload_path(staged["path"] if staged else record["path"])
    docs.set_status(id, "processing")
    try:
        built = await build(ctx, id, name, path)
        docs.progress(
            id,
            "publishing",
            "Publishing the searchable passages",
            len(built.chunks),
            len(built.chunks),
        )
        count = publish(ctx, id, built)
        docs.progress(id, "ready", "Ready to answer questions", count, count)
        clean_uploads(ctx)
    except Exception as exc:
        docs.fail(id, str(exc) or type(exc).__name__)
        docs.progress(id, "failed", "Document processing failed; see the error")


def queue_reindex(ctx: AppContext, ids: list[str]) -> int:
    """Queue documents for a rebuild; returns how many fitted in the (bounded) queue."""
    queued = 0
    for id in ids:
        if ctx.queue.full():
            break
        ctx.store.documents.requeue(id)
        ctx.store.documents.progress(id, "queued", "Queued for re-indexing")
        ctx.queue.put_nowait(id)
        queued += 1
    return queued


async def worker(ctx: AppContext) -> None:
    docs = ctx.store.documents
    while True:
        id = await ctx.queue.get()
        try:
            await asyncio.wait_for(process(ctx, id), timeout=ctx.settings.job_timeout)
        except Exception as exc:
            error = (
                "Processing timed out; retry the upload"
                if isinstance(exc, TimeoutError)
                else str(exc)
            )
            docs.fail(id, error)
            docs.progress(id, "failed", error)
        finally:
            ctx.queue.task_done()
