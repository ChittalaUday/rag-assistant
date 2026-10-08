"""PostgreSQL persistence: one repository per aggregate behind a single facade.

All SQL lives in this package; services and the pipeline call repository methods and group
multi-step writes with ``store.transaction()``.
"""

from contextlib import AbstractContextManager

from knowledge_engine.store.base import Db, Row, vector_literal
from knowledge_engine.store.cache import ExtractionCacheRepo
from knowledge_engine.store.chunks import ChunkRepo, ChunkRow
from knowledge_engine.store.conversations import ConversationRepo
from knowledge_engine.store.documents import DocumentRepo
from knowledge_engine.store.graph import GraphRepo

__all__ = ["ChunkRow", "Row", "Store", "vector_literal"]


class Store:
    def __init__(self, url: str):
        self.db = Db(url)
        self.documents = DocumentRepo(self.db)
        self.chunks = ChunkRepo(self.db)
        self.graph = GraphRepo(self.db)
        self.conversations = ConversationRepo(self.db)
        self.cache = ExtractionCacheRepo(self.db)

    def transaction(self) -> AbstractContextManager[None]:
        return self.db.transaction()

    def close(self) -> None:
        self.db.close()
