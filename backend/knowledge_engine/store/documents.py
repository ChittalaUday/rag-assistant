from typing import Any

from knowledge_engine.errors import AppError
from knowledge_engine.store.base import Db, Row


class DocumentRepo:
    def __init__(self, db: Db):
        self.db = db

    @staticmethod
    def _public(d: Row) -> Row:
        d = dict(d)
        d.pop("path", None)
        d["created_at"] = d["created_at"].isoformat()
        if d["processing_stage"] is None:
            d["processing_stage"] = d["status"]
            d["processing_message"] = d["status"].capitalize()
            d["processed_chunks"] = d["chunk_count"] if d["status"] == "ready" else 0
            d["total_chunks"] = d["chunk_count"]
        return d

    def get(self, id: str) -> Row:
        row = self.db.one("SELECT * FROM documents WHERE id=%s", (id,))
        if row is None:
            raise AppError(404, "Document not found")
        return self._public(row)

    def all(self) -> list[Row]:
        return [
            self._public(r) for r in self.db.rows("SELECT * FROM documents ORDER BY created_at, id")
        ]

    def record(self, id: str) -> Row | None:
        return self.db.one("SELECT * FROM documents WHERE id=%s", (id,))

    def pending(self, id: str) -> Row | None:
        return self.db.one("SELECT * FROM pending_documents WHERE document_id=%s", (id,))

    def has_pending(self, id: str) -> bool:
        return self.pending(id) is not None

    def path(self, id: str) -> str:
        row = self.db.one("SELECT path FROM documents WHERE id=%s", (id,))
        return str(row["path"]) if row else ""

    def find_by_hash(self, digest: str) -> str | None:
        row = self.db.one(
            "SELECT id FROM documents WHERE content_hash=%s UNION "
            "SELECT document_id FROM pending_documents WHERE content_hash=%s",
            (digest, digest),
        )
        return str(row["id"]) if row else None

    def referenced_paths(self) -> set[str]:
        rows = self.db.rows("SELECT path FROM documents UNION SELECT path FROM pending_documents")
        return {r["path"] for r in rows}

    def add(self, id: str, name: str, digest: str, path: str, labels: list[str]) -> None:
        self.db.run(
            "INSERT INTO documents(id,name,content_hash,status,path,labels) "
            "VALUES(%s,%s,%s,'queued',%s,%s)",
            (id, name, digest, path, labels),
        )

    def stage_replacement(self, id: str, name: str, digest: str, path: str) -> None:
        with self.db.transaction():
            self.db.run(
                "INSERT INTO pending_documents VALUES(%s,%s,%s,%s) ON CONFLICT (document_id) "
                "DO UPDATE SET name=EXCLUDED.name, content_hash=EXCLUDED.content_hash, "
                "path=EXCLUDED.path",
                (id, name, digest, path),
            )
            self.db.run("UPDATE documents SET status='queued', error=NULL WHERE id=%s", (id,))

    def set_status(self, id: str, status: str) -> None:
        self.db.run("UPDATE documents SET status=%s WHERE id=%s", (status, id))

    def requeue(self, id: str) -> None:
        self.db.run("UPDATE documents SET status='queued', error=NULL WHERE id=%s", (id,))

    def fail(self, id: str, error: str) -> None:
        self.db.run("UPDATE documents SET status='failed', error=%s WHERE id=%s", (error, id))

    def restore_ready(self, id: str) -> None:
        """A failed replacement of identical content: keep the published version."""
        with self.db.transaction():
            self.db.run("DELETE FROM pending_documents WHERE document_id=%s", (id,))
            self.db.run("UPDATE documents SET status='ready', error=NULL WHERE id=%s", (id,))

    def requeue_interrupted(self) -> list[str]:
        """After a restart, 'processing' jobs resume as 'queued'; returns all queued ids."""
        self.db.run("UPDATE documents SET status='queued' WHERE status='processing'")
        return [r["id"] for r in self.db.rows("SELECT id FROM documents WHERE status='queued'")]

    # built by an older pipeline, or embedded with another model than the configured one
    _OUTDATED = (
        "(d.pipeline_version < %(v)s OR EXISTS (SELECT 1 FROM chunks c WHERE c.document_id=d.id "
        "AND c.embedding_model <> %(m)s)) AND d.status IN ('ready','failed')"
    )

    def outdated(self, version: int, embedding_model: str) -> list[str]:
        rows = self.db.rows(
            f"SELECT d.id FROM documents d WHERE {self._OUTDATED} ORDER BY d.created_at, d.id",
            {"v": version, "m": embedding_model},
        )
        return [r["id"] for r in rows]

    def reembedding_needed(self, embedding_model: str) -> list[str]:
        """Ready documents with chunks embedded by another model (their vectors may be absent)."""
        rows = self.db.rows(
            "SELECT d.id FROM documents d WHERE d.status='ready' AND EXISTS "
            "(SELECT 1 FROM chunks c WHERE c.document_id=d.id AND c.embedding_model <> %s) "
            "ORDER BY d.created_at, d.id",
            (embedding_model,),
        )
        return [r["id"] for r in rows]

    def progress(
        self, id: str, stage: str, message: str, completed: int = 0, total: int = 0
    ) -> None:
        self.db.run(
            "UPDATE documents SET processing_stage=%s, processing_message=%s, "
            "processed_chunks=%s, total_chunks=%s WHERE id=%s",
            (stage, message, completed, total, id),
        )

    def text(self, id: str) -> str | None:
        row = self.db.one("SELECT text FROM document_text WHERE document_id=%s", (id,))
        return str(row["text"]) if row else None

    def save_text(self, id: str, text: str) -> None:
        self.db.run(
            "INSERT INTO document_text VALUES(%s,%s) ON CONFLICT (document_id) "
            "DO UPDATE SET text=EXCLUDED.text",
            (id, text),
        )

    def mark_ready(self, id: str, chunk_count: int, warnings: list[str], version: int) -> None:
        """Final publish step: adopt a staged replacement, then flip the document to ready."""
        import json

        staged = self.pending(id)
        if staged:
            self.db.run(
                "UPDATE documents SET name=%s, content_hash=%s, path=%s WHERE id=%s",
                (staged["name"], staged["content_hash"], staged["path"], id),
            )
            self.db.run("DELETE FROM pending_documents WHERE document_id=%s", (id,))
        self.db.run(
            "UPDATE documents SET status='ready', chunk_count=%s, error=NULL, warnings=%s::jsonb, "
            "pipeline_version=%s WHERE id=%s",
            (chunk_count, json.dumps(sorted(set(warnings))), version, id),
        )

    def delete(self, id: str) -> None:
        self.db.run("DELETE FROM documents WHERE id=%s", (id,))  # cascades to everything derived

    def labels(self) -> dict[str, list[str]]:
        rows = self.db.rows("SELECT id, labels FROM documents")
        return {r["id"]: list(r["labels"]) for r in rows}

    def stats(self, current_version: int, embedding_model: str) -> dict[str, Any]:
        row = self.db.one(
            "SELECT count(*)::int AS documents, "
            f"(count(*) FILTER (WHERE {self._OUTDATED}))::int AS stale FROM documents d",
            {"v": current_version, "m": embedding_model},
        )
        return dict(row or {})
