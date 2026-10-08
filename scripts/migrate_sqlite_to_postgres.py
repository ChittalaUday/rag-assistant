"""One-time copy of the legacy SQLite database into PostgreSQL.

    PYTHONPATH=backend uv run --no-sync python scripts/migrate_sqlite_to_postgres.py \
        [--sqlite data/assistant.sqlite3] [--database-url postgresql://localhost/knowledge_engine]

Runs Alembic migrations first, refuses a non-empty target, copies everything in one
transaction (all-or-nothing) and verifies row counts. The SQLite file is left untouched.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import psycopg
from knowledge_engine.config import Settings
from knowledge_engine.db.migrate import upgrade
from psycopg.types.json import Jsonb

TABLES = ["documents", "chunks", "nodes", "edges", "conversations", "messages"]


def copy(src: sqlite3.Connection, pg: psycopg.Connection) -> dict[str, int]:
    src.row_factory = sqlite3.Row
    rows = lambda sql: src.execute(sql).fetchall()  # noqa: E731
    progress = {r["id"]: json.loads(r["payload"]) for r in rows("SELECT * FROM document_progress")}
    for d in rows("SELECT * FROM documents ORDER BY rowid"):
        p = progress.get(d["id"], {})
        pg.execute(
            "INSERT INTO documents(id,name,content_hash,status,chunk_count,created_at,error,"
            "warnings,path,processing_stage,processing_message,processed_chunks,total_chunks) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                d["id"], d["name"], d["content_hash"], d["status"], d["chunk_count"],
                d["created_at"], d["error"], Jsonb(json.loads(d["warnings"] or "[]")), d["path"],
                p.get("processing_stage"), p.get("processing_message"),
                p.get("processed_chunks"), p.get("total_chunks"),
            ),
        )  # fmt: skip
    for r in rows("SELECT * FROM pending_documents ORDER BY rowid"):
        pg.execute(
            "INSERT INTO pending_documents VALUES(%s,%s,%s,%s)",
            (r["id"], r["name"], r["content_hash"], r["path"]),
        )
    for r in rows("SELECT * FROM document_text ORDER BY rowid"):
        pg.execute("INSERT INTO document_text VALUES(%s,%s)", (r["id"], r["text"]))
    for c in rows("SELECT * FROM chunks ORDER BY rowid"):
        pg.execute(
            "INSERT INTO chunks(id,document_id,text,embedding,page) VALUES(%s,%s,%s,%s,%s)",
            (c["id"], c["document_id"], c["text"], json.loads(c["embedding"]), c["page"]),
        )
    for n in rows("SELECT * FROM nodes ORDER BY rowid"):
        pg.execute(
            "INSERT INTO nodes VALUES(%s,%s,%s,%s)",
            (n["id"], n["label"], n["type"], n["document_id"]),
        )
    for e in rows("SELECT * FROM edges ORDER BY rowid"):
        pg.execute(
            "INSERT INTO edges(id,source,target,label,chunk_id,quote) VALUES(%s,%s,%s,%s,%s,%s)",
            (e["id"], e["source"], e["target"], e["label"], e["chunk_id"], e["quote"]),
        )
    scopes = {r["id"]: r["scope"] for r in rows("SELECT * FROM conversation_scope")}
    for c in rows("SELECT * FROM conversations ORDER BY rowid"):
        pg.execute(
            "INSERT INTO conversations(id,title,created_at,scope) VALUES(%s,%s,%s,%s)",
            (c["id"], c["title"], c["created_at"], scopes.get(c["id"])),
        )
    for m in rows("SELECT * FROM messages ORDER BY rowid"):
        pg.execute(
            "INSERT INTO messages(conversation_id,payload) VALUES(%s,%s)",
            (m["conversation_id"], Jsonb(json.loads(m["payload"]))),
        )
    return {t: src.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sqlite", type=Path, default=Settings().data_dir / "assistant.sqlite3")
    parser.add_argument("--database-url", default=Settings().database_url)
    args = parser.parse_args()
    if not args.sqlite.is_file():
        print(f"SQLite file not found: {args.sqlite}", file=sys.stderr)
        return 1
    upgrade(args.database_url, "0001")  # the schema the legacy data fits
    src = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)
    with psycopg.connect(args.database_url) as pg:
        if any(pg.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone() for t in TABLES):
            print("Target database is not empty; refusing to migrate.", file=sys.stderr)
            return 1
        expected = copy(src, pg)
        actual = {t: pg.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}  # type: ignore[index]
        if actual != expected:
            print(f"Row count mismatch, rolling back: {expected} != {actual}", file=sys.stderr)
            return 1
    upgrade(args.database_url)  # then convert to the current schema (derived graph is rebuilt)
    print("Migrated:", ", ".join(f"{t}={n}" for t, n in actual.items()))
    print("Run `make reindex` to rebuild entities and relations with the current pipeline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
