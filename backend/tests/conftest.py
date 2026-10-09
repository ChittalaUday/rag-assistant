import os
import uuid
from urllib.parse import quote

import psycopg
import pytest
from knowledge_engine.config import Settings
from knowledge_engine.store import Store
from knowledge_engine.store.migrate import upgrade

TEST_DATABASE_URL = os.environ.get(
    "KE_TEST_DATABASE_URL", "postgresql://localhost:5432/knowledge_engine_test"
)
EXTENSIONS = ("vector", "pg_trgm")


def _ensure_database() -> None:
    """Create the test database and its extensions (in `public`, shared by every test schema)."""
    try:
        conn = psycopg.connect(TEST_DATABASE_URL, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as exc:
        if "does not exist" not in str(exc):
            raise
        base, _, name = TEST_DATABASE_URL.rpartition("/")
        with psycopg.connect(base + "/postgres", autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{name.split("?")[0]}"')
        conn = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
    with conn:
        if conn.execute("SELECT to_regclass('public.documents')").fetchone()[0] is not None:
            raise RuntimeError(
                f"{TEST_DATABASE_URL} holds application tables in public; tests need a dedicated "
                "database (KE_TEST_DATABASE_URL). Refusing to run."
            )
        for ext in EXTENSIONS:
            conn.execute(f"CREATE EXTENSION IF NOT EXISTS {ext} WITH SCHEMA public")


@pytest.fixture(autouse=True)
def database(monkeypatch):
    """Every test runs in its own schema; the app (or the `store` fixture) migrates it."""
    _ensure_database()
    schema = "t_" + uuid.uuid4().hex[:12]
    sep = "&" if "?" in TEST_DATABASE_URL else "?"
    url = f"{TEST_DATABASE_URL}{sep}options={quote(f'-csearch_path={schema},public')}"
    monkeypatch.setenv("KE_DATABASE_URL", url)
    monkeypatch.setitem(Settings.model_config, "env_file", None)  # tests ignore the dev .env
    # the fake model's embedding (fakes.py): 384 hashed buckets, no task prefixes
    for key, value in {
        "KE_EMBEDDING_MODEL": "all-minilm:l6-v2",
        "KE_EMBEDDING_DIMENSIONS": "384",
        "KE_EMBEDDING_DOCUMENT_PREFIX": "",
        "KE_EMBEDDING_QUERY_PREFIX": "",
        # model-backed features are off unless a test turns them on
        "KE_RERANK_MODEL": "",
        "KE_CONTEXTUAL_CHUNKS": "false",
        "KE_SECOND_HOP": "off",
    }.items():
        monkeypatch.setenv(key, value)
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
        yield
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture
def store():
    """A migrated store on this test's schema (for tests that bypass the API)."""
    settings = Settings()
    upgrade(settings.database_url, "head", settings.embedding_dimensions, "fake-embed")
    s = Store(settings.database_url)
    yield s
    s.close()
