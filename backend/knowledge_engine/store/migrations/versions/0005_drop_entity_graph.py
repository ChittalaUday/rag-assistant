"""Remove the entity graph; keep one cache for model outputs.

Measured end to end, the extracted entities and relations did not improve answers (0.758 correct
with and without them on 124 questions) and extraction cost 15-20 s per passage, so the engine
stops extracting them. The extraction cache becomes a general cache of model outputs (it now holds
the contextual passage summaries); its old entries were extractions and are dropped.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS relation_evidence;
    DROP TABLE IF EXISTS relations;
    DROP TABLE IF EXISTS entity_mentions;
    DROP TABLE IF EXISTS entity_aliases;
    DROP TABLE IF EXISTS entities;
    ALTER TABLE extraction_cache RENAME TO model_cache;
    DELETE FROM model_cache;
    """)


def downgrade() -> None:
    raise NotImplementedError("0005 drops the entity graph; restore from a backup instead")
