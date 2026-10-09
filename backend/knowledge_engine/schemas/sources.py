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


class GraphEntity(BaseModel):
    id: str
    name: str
    typed: bool  # named in the typed text
    found: bool  # named in a passage (False: typed only)


class GraphRelation(BaseModel):
    source: str
    target: str
    label: str  # the words between the two names in the quoted sentence
    chunk_id: str
    document: str
    quote: str
    script: str = ""  # script most of its document is written in ("LATIN", "TELUGU", ...)


class PreviewGraph(BaseModel):
    entities: list[GraphEntity] = []
    relations: list[GraphRelation] = []


class Preview(BaseModel):
    """Passages a piece of question text is likely to draw on, found while it is typed."""

    passages: list[PreviewPassage]
    corrections: dict[str, str]  # typed word -> corpus word
    graph: PreviewGraph = PreviewGraph()


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
