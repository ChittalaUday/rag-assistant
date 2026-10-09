from typing import Literal

from pydantic import BaseModel

from knowledge_engine.schemas.sources import Citation

DocumentStatus = Literal["queued", "processing", "ready", "failed"]


class Document(BaseModel):
    id: str
    name: str
    content_hash: str
    status: DocumentStatus
    chunk_count: int
    created_at: str
    error: str | None = None
    warnings: list[str]
    labels: list[str] = []
    pipeline_version: int = 1
    # Counts describe the current processing stage, not overall percentage.
    processing_stage: str
    processing_message: str
    processed_chunks: int
    total_chunks: int


class DocumentList(BaseModel):
    documents: list[Document]


class ReindexResponse(BaseModel):
    queued: int


class DocumentContent(BaseModel):
    document: Document
    text: str
    text_kind: Literal["extracted", "original", "reconstructed"]
    showing_last_published_version: bool
    provenance_note: str | None = None
    chunks: list[Citation]
    source_urls: list[str]
