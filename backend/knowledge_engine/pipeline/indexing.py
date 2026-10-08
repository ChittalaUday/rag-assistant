"""Indexing pipeline: documents in, searchable chunks + entity/relation graph out.

    parse -> normalise -> chunk -> embed -> extract -> validate      (slow, no database writes)
          -> resolve entities -> write chunks/entities/relations     (one atomic transaction)

A failure anywhere before the transaction leaves the previously published version untouched.
Domain knowledge lives in the ontology file, never here.
"""

import asyncio
import logging
import math
import re
import uuid
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from knowledge_engine.context import AppContext
from knowledge_engine.extraction.resolver import resolve, scan_mentions
from knowledge_engine.extraction.validator import ChunkExtraction, Mention, validate_extraction
from knowledge_engine.ingestion.chunker import Chunk, chunk_document
from knowledge_engine.ingestion.parser import parse
from knowledge_engine.ingestion.quality import extractable
from knowledge_engine.store import ChunkRow
from knowledge_engine.text import name_key, normalize_text

log = logging.getLogger(__name__)
PIPELINE_VERSION = 3  # 3: section headings count as evidence for a relation's endpoints
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
    extractions: list[ChunkExtraction | None]
    warnings: list[str] = field(default_factory=list)


async def build(ctx: AppContext, id: str, name: str, path: Path, extract: bool = True) -> Built:
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

    stage = "Extracting source-backed entities and relationships"
    store.documents.progress(id, "extracting", stage, 0, len(chunks))
    flags = extractable(chunks) if extract else [False] * len(chunks)  # retrieval-only indexing
    extractions: list[ChunkExtraction | None] = []
    skipped = failed = 0
    for n, (chunk, ok) in enumerate(zip(chunks, flags, strict=True), 1):
        if not ok:
            skipped += extract  # only a quality-gate skip counts, not extraction being disabled
            extractions.append(None)
        else:
            try:
                raw = await ctx.extractor.extract(title, chunk)
                extractions.append(validate_extraction(raw, chunk.text, ctx.ontology))
            except (
                ValueError
            ):  # malformed model output for this passage; infrastructure errors raise
                failed += 1
                extractions.append(None)
        store.documents.progress(id, "extracting", stage, n, len(chunks))

    counts: Counter[str] = Counter()
    for e in extractions:
        if e:
            counts.update(e.warnings)
    for message, n in sorted(counts.items()):
        log.info("%s: %s (x%d)", name, message, n)
    entities = sum(n for m, n in counts.items() if not m.startswith("Rejected relationship"))
    relations = sum(n for m, n in counts.items() if m.startswith("Rejected relationship"))
    warnings = []
    if entities or relations:
        warnings.append(
            f"Validation discarded {entities} unsupported entities and {relations} unsupported "
            "relationships (not found in the text, wrong type, or without verbatim evidence)"
        )
    if extract and flags and not any(e and (e.entities or e.relations) for e in extractions):
        warnings.append(
            "No entities or relationships were found: forms, questionnaires and checklists state "
            "few facts. The text is still searchable and answers can quote it"
        )
    if skipped:
        warnings.append(
            f"{skipped} repetitive or empty passage(s), such as form templates, are searchable "
            "but were not analysed for entities and relations"
        )
    if failed:
        warnings.append(
            f"Model output could not be parsed for {failed} passage(s); they were skipped"
        )
    return Built(text, chunks, contexts, vectors, extractions, warnings)


def publish(ctx: AppContext, id: str, built: Built) -> int:
    """Atomically replace the document's chunks, entities, mentions and relations."""
    store, ontology = ctx.store, ctx.ontology
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
        mentions: list[Mention] = [m for e in built.extractions if e for m in e.entities]
        resolution = resolve(store.graph, ontology, mentions)
        store.graph.add_mentions(scan_mentions([(r.id, r.text) for r in rows], resolution.surfaces))

        def entity_of(name: str, type_: str) -> str:
            return resolution.ids[(ontology.types[type_].cls, name_key(name))]

        evidence: list[tuple[str, str, str | None, str, int, int, float, str]] = []
        seen: set[tuple[str, int, int]] = set()  # chunk overlap repeats a sentence in two chunks
        for row, extraction in zip(rows, built.extractions, strict=True):
            for r in extraction.relations if extraction else []:
                subject, obj = (
                    entity_of(r.subject, r.subject_type),
                    entity_of(r.object, r.object_type),
                )
                if subject == obj:
                    continue  # two spellings of one entity: not a relation
                if ontology.predicates[r.predicate].symmetric and subject > obj:
                    subject, obj = obj, subject
                rid = relation_id(subject, r.predicate, obj)
                origin = row.char_start or 0
                span = (rid, origin + r.quote_start, origin + r.quote_end)
                if span in seen:
                    continue  # the same words of the document: one piece of evidence, not two
                seen.add(span)
                store.graph.add_relation(rid, subject, r.predicate, obj)
                evidence.append(
                    (rid, row.id, r.phrase, r.quote, r.quote_start, r.quote_end, r.confidence,
                     ctx.extractor.version)
                )  # fmt: skip
        store.graph.add_evidence(evidence)
        store.graph.gc()
        store.chunks.refresh_lexicon()
        store.documents.mark_ready(id, len(rows), built.warnings, PIPELINE_VERSION)
    return len(rows)


def relation_id(subject: str, predicate: str, obj: str) -> str:
    import hashlib

    return "rel_" + hashlib.sha1(f"{subject}|{predicate}|{obj}".encode()).hexdigest()[:20]


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


async def process(ctx: AppContext, id: str, extract: bool = True) -> None:
    docs = ctx.store.documents
    record = docs.record(id)
    if not record:
        return
    staged = docs.pending(id)
    name = staged["name"] if staged else record["name"]
    path = ctx.upload_path(staged["path"] if staged else record["path"])
    docs.set_status(id, "processing")
    try:
        built = await build(ctx, id, name, path, extract)
        docs.progress(
            id,
            "publishing",
            "Publishing validated source evidence",
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
