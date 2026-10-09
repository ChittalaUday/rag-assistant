from typing import Any

from psycopg.types.json import Jsonb

from knowledge_engine.store.base import Db


class ModelCacheRepo:
    """Model outputs keyed by model, prompt version and input, so re-indexing unchanged text
    makes no model calls (see ingestion/situate)."""

    def __init__(self, db: Db):
        self.db = db

    def get(self, key: str) -> dict[str, Any] | None:
        row = self.db.one("SELECT payload FROM model_cache WHERE key=%s", (key,))
        return dict(row["payload"]) if row else None

    def put(self, key: str, payload: dict[str, Any]) -> None:
        self.db.run(
            "INSERT INTO model_cache VALUES(%s,%s) ON CONFLICT (key) DO NOTHING",
            (key, Jsonb(payload)),
        )
