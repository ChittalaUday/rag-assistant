from pydantic import BaseModel


class IndexStats(BaseModel):
    documents: int = 0
    stale_documents: int = 0  # indexed by an older pipeline version; run reindex
    chunks: int = 0
    entities: int = 0
    relations: int = 0


class Health(BaseModel):
    status: str
    ollama_available: bool
    models: list[str]
    chat_model: str
    embedding_model: str
    warnings: list[str]
    index: IndexStats = IndexStats()
