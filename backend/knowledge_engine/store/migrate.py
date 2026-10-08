from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

MIGRATIONS = Path(__file__).parent / "migrations"


def upgrade(
    url: str,
    revision: str = "head",
    embedding_dimensions: int = 384,
    embedding_model: str = "all-minilm:l6-v2",
) -> None:
    """Apply schema migrations. The embedding settings only matter when the vector column is
    first created (migration 0002); later runs ignore them."""
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.attributes["url"] = url
    cfg.attributes["embedding_dimensions"] = embedding_dimensions
    cfg.attributes["embedding_model"] = embedding_model
    command.upgrade(cfg, revision)


def align_embedding_dimensions(url: str, dimensions: int) -> bool:
    """Make the chunk vector column match the configured embedding size; True if it changed.

    The size is fixed when the column is created and vectors of one size cannot be converted to
    another, so a different model means a new, empty (NULL) column. Chunk text is untouched; the
    documents are re-embedded afterwards (their chunks still name the old model, so they count as
    outdated).
    """
    with psycopg.connect(url, autocommit=True) as conn:
        row = conn.execute(
            "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'chunks'::regclass "
            "AND attname = 'embedding'"
        ).fetchone()
        if row is None or row[0] == dimensions:
            return False
        with conn.transaction():
            conn.execute("DROP INDEX IF EXISTS chunks_embedding_idx")
            conn.execute("ALTER TABLE chunks DROP COLUMN embedding")
            conn.execute(f"ALTER TABLE chunks ADD COLUMN embedding vector({int(dimensions)})")
            conn.execute(
                "CREATE INDEX chunks_embedding_idx ON chunks "
                "USING hnsw (embedding vector_cosine_ops)"
            )
    return True
