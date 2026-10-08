from typing import Any

from psycopg.types.json import Jsonb

from knowledge_engine.store.base import Db


class ExtractionCacheRepo:
    """Raw model extractions keyed by model, ontology, prompt and passage (see extractor)."""

    def __init__(self, db: Db):
        self.db = db

    def get(self, key: str) -> dict[str, Any] | None:
        row = self.db.one("SELECT payload FROM extraction_cache WHERE key=%s", (key,))
        return dict(row["payload"]) if row else None

    def put(self, key: str, payload: dict[str, Any]) -> None:
        self.db.run(
            "INSERT INTO extraction_cache VALUES(%s,%s) ON CONFLICT (key) DO NOTHING",
            (key, Jsonb(payload)),
        )
