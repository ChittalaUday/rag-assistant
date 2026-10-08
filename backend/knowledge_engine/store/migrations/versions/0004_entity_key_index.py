"""Index entity names for exact lookup.

Linking a question to entities looks names up by key alone; the unique index starts with the entity
class, so every question scanned every entity (linear in the size of the graph).

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS entities_key_idx ON entities (key)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS entities_key_idx")
