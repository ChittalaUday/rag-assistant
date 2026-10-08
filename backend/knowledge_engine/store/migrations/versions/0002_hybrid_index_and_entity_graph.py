"""Hybrid chunk index (pgvector + full text) and a resolved entity/relation graph.

Chunks keep their text and embeddings (converted to a pgvector column), gain offsets, headings and
a generated full-text column. The per-document node/edge tables are replaced by canonical
entities with aliases, mentions, typed relations and per-chunk evidence; that derived graph is
rebuilt by re-indexing (`python -m knowledge_engine reindex`); documents are the source of truth.

Revision ID: 0002
Revises: 0001
"""

from alembic import context, op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    attrs = context.config.attributes
    dims = int(attrs.get("embedding_dimensions", 384))
    model = str(attrs.get("embedding_model", "all-minilm:l6-v2")).replace("'", "''")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public")

    op.execute(f"""
    ALTER TABLE documents
        ADD COLUMN labels text[] NOT NULL DEFAULT '{{}}',
        ADD COLUMN pipeline_version integer NOT NULL DEFAULT 1;
    -- documents that announced themselves as test data keep that provenance as a label
    UPDATE documents d SET labels = '{{synthetic}}' WHERE EXISTS (
        SELECT 1 FROM chunks c WHERE c.document_id = d.id
        AND c.text ILIKE '%fictional test scenario%');

    ALTER TABLE chunks
        ADD COLUMN position integer,
        ADD COLUMN heading text,
        ADD COLUMN context text NOT NULL DEFAULT '',
        ADD COLUMN char_start integer,
        ADD COLUMN char_end integer,
        ADD COLUMN embedding_model text NOT NULL DEFAULT '{model}',
        ADD COLUMN vec vector({dims});
    UPDATE chunks c SET position = r.rn - 1
        FROM (SELECT id, row_number() OVER (PARTITION BY document_id ORDER BY seq) rn
              FROM chunks) r WHERE r.id = c.id;
    UPDATE chunks c SET context = d.name FROM documents d WHERE d.id = c.document_id;
    UPDATE chunks c SET char_start = strpos(t.text, c.text) - 1,
                        char_end = strpos(t.text, c.text) - 1 + char_length(c.text)
        FROM document_text t WHERE t.document_id = c.document_id AND strpos(t.text, c.text) > 0;
    UPDATE chunks SET vec = embedding::vector;
    ALTER TABLE chunks DROP COLUMN embedding;
    ALTER TABLE chunks RENAME COLUMN vec TO embedding;
    ALTER TABLE chunks ALTER COLUMN embedding SET NOT NULL;
    ALTER TABLE chunks ALTER COLUMN position SET NOT NULL;
    ALTER TABLE chunks ADD COLUMN fts tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(context, '')), 'A') ||
        setweight(to_tsvector('english', text), 'B')) STORED;
    CREATE INDEX chunks_fts_idx ON chunks USING gin (fts);
    CREATE INDEX chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);

    DROP TABLE edges;
    DROP TABLE nodes;
    ALTER TABLE conversations DROP COLUMN scope;

    CREATE TABLE entities (
        id text PRIMARY KEY,
        type text NOT NULL,
        class text NOT NULL,
        canonical_name text NOT NULL,
        key text NOT NULL,
        UNIQUE (class, key)
    );
    CREATE INDEX entities_key_trgm_idx ON entities USING gin (key gin_trgm_ops);

    CREATE TABLE entity_aliases (
        entity_id text NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
        alias text NOT NULL,
        key text NOT NULL,
        PRIMARY KEY (entity_id, key)
    );
    CREATE INDEX entity_aliases_key_idx ON entity_aliases (key);
    CREATE INDEX entity_aliases_key_trgm_idx ON entity_aliases USING gin (key gin_trgm_ops);

    CREATE TABLE entity_mentions (
        entity_id text NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
        chunk_id text NOT NULL REFERENCES chunks (id) ON DELETE CASCADE,
        count integer NOT NULL,
        PRIMARY KEY (entity_id, chunk_id)
    );
    CREATE INDEX entity_mentions_chunk_idx ON entity_mentions (chunk_id);

    CREATE TABLE relations (
        id text PRIMARY KEY,
        subject_id text NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
        predicate text NOT NULL,
        object_id text NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
        CHECK (subject_id <> object_id)
    );
    CREATE INDEX relations_subject_idx ON relations (subject_id);
    CREATE INDEX relations_object_idx ON relations (object_id);

    CREATE TABLE relation_evidence (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        relation_id text NOT NULL REFERENCES relations (id) ON DELETE CASCADE,
        chunk_id text NOT NULL REFERENCES chunks (id) ON DELETE CASCADE,
        phrase text,
        quote text NOT NULL,
        quote_start integer NOT NULL,
        quote_end integer NOT NULL,
        confidence real NOT NULL,
        extractor text NOT NULL,
        UNIQUE (relation_id, chunk_id, quote_start, quote_end)
    );
    CREATE INDEX relation_evidence_chunk_idx ON relation_evidence (chunk_id);

    CREATE TABLE extraction_cache (
        key text PRIMARY KEY,
        payload jsonb NOT NULL
    );
    """)


def downgrade() -> None:
    raise NotImplementedError("0002 drops the legacy graph tables; restore from a backup instead")
