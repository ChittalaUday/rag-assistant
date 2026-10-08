"""Shared database handle: one autocommit psycopg connection used from the event-loop thread.

Calls are short and never interleave mid-call; multi-statement writes use ``transaction()``.
ponytail: single sync connection; move to a pool/async driver if request concurrency matters.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

Row = dict[str, Any]
Args = tuple[Any, ...] | list[Any] | dict[str, Any]


class Db:
    def __init__(self, url: str):
        self.conn: psycopg.Connection[Row] = psycopg.connect(
            url, autocommit=True, row_factory=dict_row
        )
        self.conn.execute("SET TIME ZONE 'UTC'")
        # pgvector: keep scanning the HNSW index when label filters discard candidates, and look at
        # more candidates per query (recall over speed at this scale).
        self.conn.execute("SET hnsw.iterative_scan = relaxed_order")
        self.conn.execute("SET hnsw.ef_search = 100")
        self.conn.execute(
            "SET pg_trgm.word_similarity_threshold = 0.4"
        )  # misspelt single-word names

    def close(self) -> None:
        self.conn.close()

    def rows(self, sql: str, args: Args = ()) -> list[Row]:
        return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args: Args = ()) -> Row | None:
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def run(self, sql: str, args: Args = ()) -> None:
        self.conn.execute(sql, args)

    def many(self, sql: str, batch: Sequence[Args]) -> None:
        if batch:
            with self.conn.cursor() as cur:
                cur.executemany(sql, batch)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.conn.transaction():
            yield


def vector_literal(values: Sequence[float]) -> str:
    """pgvector text form; avoids needing a client-side type adapter."""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"
