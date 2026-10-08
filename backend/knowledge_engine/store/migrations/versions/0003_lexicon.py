"""Corpus vocabulary for query spelling correction.

Every word in the indexed text (no stemming) with its document count, trigram-indexed so a
misspelt query word can be matched to the closest real word. A plain table rather than a
materialized view: PostgreSQL 17+ refreshes views with a restricted search_path, which breaks the
unqualified table name inside ts_stat(). Rebuilt whenever evidence is published or deleted.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"

REBUILD = """
INSERT INTO lexicon(word, ndoc)
SELECT word, ndoc::int FROM ts_stat('SELECT to_tsvector(''simple'', text) FROM chunks')
WHERE char_length(word) >= 3
"""


def upgrade() -> None:
    op.execute(f"""
    CREATE TABLE lexicon (word text PRIMARY KEY, ndoc integer NOT NULL);
    CREATE INDEX lexicon_word_trgm_idx ON lexicon USING gin (word gin_trgm_ops);
    {REBUILD};
    """)


def downgrade() -> None:
    op.execute("DROP TABLE lexicon")
