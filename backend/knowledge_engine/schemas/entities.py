from typing import Any

from pydantic import BaseModel, Field

from knowledge_engine.schemas.graph import Edge


class EntitySummary(BaseModel):
    id: str
    name: str
    type: str
    degree: int  # number of relations
    mentions: int


class EntityList(BaseModel):
    entities: list[EntitySummary]


class EntityDocument(BaseModel):
    id: str
    name: str
    mentions: int


class EntityDetail(BaseModel):
    id: str
    name: str
    type: str
    aliases: list[str]
    documents: list[EntityDocument]
    relations: list[Edge]


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
    ranks: dict[str, int]  # per retriever (dense/sparse/graph), 1 = best


class SearchResponse(BaseModel):
    hits: list[SearchHit]
    plan: dict[str, Any]
