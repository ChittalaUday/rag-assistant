from psycopg.types.json import Jsonb

from knowledge_engine.errors import AppError
from knowledge_engine.store.base import Db, Row


class ConversationRepo:
    def __init__(self, db: Db):
        self.db = db

    def all(self) -> list[Row]:
        rows = self.db.rows(
            "SELECT id, title, created_at FROM conversations ORDER BY created_at DESC"
        )
        return [{**r, "created_at": r["created_at"].isoformat()} for r in rows]

    def messages(self, id: str) -> list[Row]:
        if not self.db.rows("SELECT id FROM conversations WHERE id=%s", (id,)):
            raise AppError(404, "Conversation not found")
        rows = self.db.rows(
            "SELECT payload FROM messages WHERE conversation_id=%s ORDER BY seq", (id,)
        )
        return [r["payload"] for r in rows]

    def every_message(self) -> list[Row]:
        """Every saved message with its conversation, oldest conversation first, in order."""
        return self.db.rows(
            "SELECT c.id AS conversation_id, c.title, m.payload FROM messages m "
            "JOIN conversations c ON c.id=m.conversation_id ORDER BY c.created_at, c.id, m.seq"
        )

    def delete(self, id: str) -> None:
        self.db.run("DELETE FROM conversations WHERE id=%s", (id,))  # cascades to messages

    def save_exchange(self, id: str, new: bool, title: str, messages: list[Row]) -> None:
        with self.db.transaction():
            if new:
                self.db.run("INSERT INTO conversations(id,title) VALUES(%s,%s)", (id, title[:80]))
            for message in messages:
                self.db.run(
                    "INSERT INTO messages(conversation_id,payload) VALUES(%s,%s)",
                    (id, Jsonb(message)),
                )
