"""Initial relational schema.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE documents (
        id text PRIMARY KEY,
        name text NOT NULL,
        content_hash text NOT NULL,
        status text NOT NULL CHECK (status IN ('queued', 'processing', 'ready', 'failed')),
        chunk_count integer NOT NULL DEFAULT 0,
        created_at timestamptz NOT NULL DEFAULT now(),
        error text,
        warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
        path text NOT NULL,
        processing_stage text,
        processing_message text,
        processed_chunks integer,
        total_chunks integer
    );
    CREATE INDEX documents_content_hash_idx ON documents (content_hash);

    CREATE TABLE pending_documents (
        document_id text PRIMARY KEY REFERENCES documents (id) ON DELETE CASCADE,
        name text NOT NULL,
        content_hash text NOT NULL,
        path text NOT NULL
    );
    CREATE INDEX pending_documents_content_hash_idx ON pending_documents (content_hash);

    CREATE TABLE document_text (
        document_id text PRIMARY KEY REFERENCES documents (id) ON DELETE CASCADE,
        text text NOT NULL
    );

    CREATE TABLE chunks (
        id text PRIMARY KEY,
        seq bigint GENERATED ALWAYS AS IDENTITY,
        document_id text NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
        text text NOT NULL,
        embedding double precision[] NOT NULL,
        page integer
    );
    CREATE INDEX chunks_document_id_idx ON chunks (document_id, seq);

    CREATE TABLE nodes (
        id text NOT NULL,
        label text NOT NULL,
        type text NOT NULL,
        document_id text NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
        PRIMARY KEY (id, document_id)
    );

    CREATE TABLE edges (
        pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        id text NOT NULL,
        source text NOT NULL,
        target text NOT NULL,
        label text NOT NULL,
        chunk_id text NOT NULL REFERENCES chunks (id) ON DELETE CASCADE,
        quote text NOT NULL
    );
    CREATE INDEX edges_chunk_id_idx ON edges (chunk_id);
    CREATE INDEX edges_id_idx ON edges (id);
    CREATE INDEX edges_source_idx ON edges (source);
    CREATE INDEX edges_target_idx ON edges (target);

    CREATE TABLE conversations (
        id text PRIMARY KEY,
        title text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        scope text
    );

    CREATE TABLE messages (
        seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        conversation_id text NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
        payload jsonb NOT NULL
    );
    CREATE INDEX messages_conversation_idx ON messages (conversation_id, seq);
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE messages, conversations, edges, nodes, chunks, document_text,
        pending_documents, documents;
    """)
