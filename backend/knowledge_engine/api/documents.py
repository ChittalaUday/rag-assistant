from typing import Annotated

from fastapi import APIRouter, Form, UploadFile

from knowledge_engine.context import Ctx
from knowledge_engine.ingestion import intake
from knowledge_engine.pipeline.indexing import PIPELINE_VERSION, queue_reindex
from knowledge_engine.schemas.common import OkResponse
from knowledge_engine.schemas.documents import (
    Document,
    DocumentChunks,
    DocumentContent,
    DocumentList,
    ReindexResponse,
)

router = APIRouter(tags=["documents"])


@router.get("/documents")
async def list_documents(ctx: Ctx) -> DocumentList:
    return DocumentList(documents=[Document.model_validate(d) for d in ctx.store.documents.all()])


@router.post("/documents", status_code=202)
async def add_document(
    file: UploadFile, ctx: Ctx, labels: Annotated[str, Form(description="comma-separated")] = ""
) -> Document:
    return Document.model_validate(await intake.upload(ctx, file, None, labels.split(",")))


@router.post("/documents/reindex", status_code=202)
async def reindex(ctx: Ctx) -> ReindexResponse:
    """Re-run the indexing pipeline for documents built by an older pipeline version."""
    ids = ctx.store.documents.outdated(PIPELINE_VERSION, ctx.settings.embedding_model)
    return ReindexResponse(queued=queue_reindex(ctx, ids))


@router.put("/documents/{id}", status_code=202)
async def replace_document(id: str, file: UploadFile, ctx: Ctx) -> Document:
    return Document.model_validate(await intake.upload(ctx, file, id))


@router.delete("/documents/{id}")
async def delete_document(id: str, ctx: Ctx) -> OkResponse:
    intake.delete_document(ctx, id)
    return OkResponse(ok=True)


@router.get("/documents/{id}/content")
async def document_content(id: str, ctx: Ctx) -> DocumentContent:
    return DocumentContent.model_validate(intake.document_content(ctx, id))


@router.get("/documents/{id}/chunks")
async def document_chunks(id: str, ctx: Ctx) -> DocumentChunks:
    """The document's searchable passages in order, with the location and summary indexed with
    each (for the document pipeline view)."""
    store = ctx.store
    document = store.documents.get(id)  # 404 when missing
    chunks = []
    for row in store.chunks.passages(id):
        location, _, summary = row["context"].partition("\n")
        chunks.append({**row, "location": location, "summary": summary.strip()})
    text = store.documents.text(id)
    return DocumentChunks.model_validate(
        {"document": document, "text_chars": len(text or ""), "chunks": chunks}
    )


@router.post("/demo", status_code=202)
async def load_demo(ctx: Ctx) -> DocumentList:
    return DocumentList.model_validate({"documents": await intake.load_demo(ctx)})
