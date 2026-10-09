from typing import Any

from pydantic import BaseModel, Field


class Citation(BaseModel):
    chunk_id: str
    document_id: str
    document_name: str
    text: str
    quote: str | None = None
    page: int | None = None
    heading: str | None = None
    source_urls: list[str] = []


class PreviewPassage(BaseModel):
    chunk_id: str
    document_id: str
    document: str
    heading: str | None = None


class Preview(BaseModel):
    """Passages a piece of question text is likely to draw on, found while it is typed."""

    passages: list[PreviewPassage]
    corrections: dict[str, str]  # typed word -> corpus word


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=10, ge=1, le=30)
    exclude_labels: list[str] = Field(default_factory=list, max_length=20)


class SearchHit(BaseModel):
    chunk_id: str
    document_id: str
    document_name: str
    heading: str | None = None
    text: str
    score: float
    ranks: dict[str, int]  # per retriever (dense/sparse/hop), 1 = best


class SearchResponse(BaseModel):
    hits: list[SearchHit]
    plan: dict[str, Any]
