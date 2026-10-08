"""Document intake: accept uploads, deduplicate by content hash, stage replacements, expose the
reader view. Heavy work is queued for the indexing pipeline."""

import hashlib
import uuid
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from knowledge_engine.context import AppContext
from knowledge_engine.errors import AppError
from knowledge_engine.ingestion.parser import DOCUMENT_SUFFIXES
from knowledge_engine.pipeline.indexing import clean_uploads
from knowledge_engine.provenance import source_urls
from knowledge_engine.text import clean_labels


async def upload(
    ctx: AppContext,
    file: UploadFile,
    replacement: str | None = None,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    docs, s = ctx.store.documents, ctx.settings
    if replacement and docs.get(replacement)["status"] in {"queued", "processing"}:
        raise AppError(409, "Document is still processing")
    if ctx.queue.full():
        raise AppError(429, "Processing queue is full; try again later")
    data = await file.read(s.max_upload_bytes + 1)
    if len(data) > s.max_upload_bytes:
        raise AppError(413, "Upload exceeds 20 MB")
    if not data:
        raise AppError(400, "Empty document")
    name = Path(file.filename or "document.txt").name
    suffix = Path(name).suffix.lower()
    if suffix not in DOCUMENT_SUFFIXES:
        raise AppError(400, "Unsupported document type")
    digest = hashlib.sha256(data).hexdigest()
    existing_id = docs.find_by_hash(digest)
    if existing_id:
        if replacement and existing_id != replacement:
            raise AppError(409, "This content already belongs to another document")
        existing = docs.get(existing_id)
        if existing["status"] == "failed":
            if existing["content_hash"] == digest and existing["chunk_count"]:
                docs.restore_ready(existing_id)
                count = existing["chunk_count"]
                docs.progress(existing_id, "ready", "Ready to answer questions", count, count)
                clean_uploads(ctx)
                return docs.get(existing_id)
            docs.requeue(existing_id)
            docs.progress(existing_id, "queued", "Queued for retry")
            ctx.queue.put_nowait(existing_id)
        return docs.get(existing_id)
    id = replacement or str(uuid.uuid4())
    path = s.data_dir / (digest + suffix)
    path.write_bytes(data)
    if replacement:
        docs.stage_replacement(id, name, digest, str(path))
    else:
        docs.add(id, name, digest, str(path), clean_labels(labels))
    docs.progress(id, "queued", "Queued for document processing")
    ctx.queue.put_nowait(id)
    return docs.get(id)


def delete_document(ctx: AppContext, id: str) -> None:
    if ctx.store.documents.get(id)["status"] == "processing":
        raise AppError(409, "Wait for processing to finish before deleting")
    with ctx.store.transaction():
        ctx.store.documents.delete(id)  # cascades to chunks, mentions and evidence
        ctx.store.graph.gc()  # ...so entities and relations nothing supports any more go too
        ctx.store.chunks.refresh_lexicon()
    clean_uploads(ctx)


async def load_demo(ctx: AppContext) -> list[dict[str, Any]]:
    """Ingest the bundled sample corpus; fixtures/testing/* is labelled as synthetic test data."""
    fixtures = ctx.settings.fixtures_dir
    sets: list[tuple[list[Path], list[str]]] = [(sorted((fixtures / "documents").glob("*.md")), [])]
    if not sets[0][0]:
        sets = [(sorted(fixtures.glob("*.md")), [])]
    if not sets[0][0]:
        raise AppError(404, "Demo fixtures are not installed")
    sets.append((sorted((fixtures / "testing").glob("*.md")), ["synthetic"]))
    result = []
    for paths, labels in sets:
        for path in paths:
            with path.open("rb") as f:
                result.append(
                    await upload(ctx, UploadFile(filename=path.name, file=f), None, labels)
                )
    return result


def document_content(ctx: AppContext, id: str) -> dict[str, Any]:
    store = ctx.store
    document = store.documents.get(id)
    chunk_ids = store.chunks.ids(id)
    citations = store.chunks.citations([(cid, None) for cid in chunk_ids])
    saved = store.documents.text(id)
    if saved is not None:
        text, kind = saved, "extracted"
    else:
        original = ctx.upload_path(store.documents.path(id))
        try:
            if original.suffix.lower() not in {".txt", ".md", ".csv"}:
                raise ValueError("Legacy binary document has no stored full parsed text")
            text, kind = original.read_text(encoding="utf-8-sig"), "original"
        except (OSError, ValueError):
            text, kind = "", "reconstructed"
            for citation in citations:
                passage = citation["text"]
                overlap = next(
                    (
                        n
                        for n in range(min(len(text), len(passage)), 0, -1)
                        if text.endswith(passage[:n])
                    ),
                    0,
                )
                text += passage[overlap:]
    showing_previous = store.documents.has_pending(id) and bool(citations)
    note = (
        "Showing the last successfully published version; the replacement is "
        "still processing or failed."
        if showing_previous
        else None
    )
    if not text:
        note = "Full parsed text is not yet available; wait for document processing."
    return {
        "document": document,
        "text": text,
        "text_kind": kind,
        "showing_last_published_version": showing_previous,
        "provenance_note": note,
        "chunks": citations,
        "source_urls": source_urls([text]),
    }
