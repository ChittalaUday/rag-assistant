import re
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
LOOPBACK_HOSTS = {"", "localhost", "127.0.0.1", "::1"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KE_", env_file=REPO_ROOT / "backend" / ".env", extra="ignore"
    )
    # storage
    data_dir: Path = Path("data")
    fixtures_dir: Path = REPO_ROOT / "fixtures"
    database_url: str = "postgresql://localhost:5432/knowledge_engine"
    auto_migrate: bool = True
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]
    # Host headers the API answers to (blocks DNS-rebinding from web pages); add a name if the
    # API is deliberately reached by another one. "testserver" is the in-process test client.
    allowed_hosts: list[str] = ["127.0.0.1", "localhost", "testserver"]
    # models (loopback Ollama only)
    ollama_url: str = "http://127.0.0.1:11434"
    chat_model: str = "qwen3.5:9b"
    embedding_model: str = "all-minilm:l6-v2"
    # Vector size the Ollama embedding model produces. Changing the model or this size replaces
    # the vector column at start-up and re-embeds the documents.
    embedding_dimensions: int = 384
    embedding_document_prefix: str = ""  # e.g. "search_document: " for nomic-embed-text
    embedding_query_prefix: str = ""  # e.g. "search_query: "
    model_timeout: float = 180
    job_timeout: float = 1800
    # ingestion
    max_upload_bytes: int = 20 * 1024 * 1024
    max_jobs: int = 32
    max_chunks: int = 600
    chunk_chars: int = Field(default=700, ge=100)  # smaller values cannot hold a sentence
    ontology_path: Path | None = None  # default: the built-in generic ontology
    # answering
    context_chars: int = 9000  # evidence budget in the answer prompt
    rewrite_followups: bool = True  # resolve "she"/"that" with one small model call
    # Should the entity graph influence which passages are retrieved? It always feeds facts,
    # conflicts and highlights. "relationship": only for relationship-type questions.
    graph_retrieval: Literal["off", "relationship", "always"] = "relationship"
    label_notices: dict[str, str] = {
        "synthetic": "Synthetic test data — not real facts.",
    }

    @field_validator("database_url")
    @classmethod
    def loopback_database(cls, url: str) -> str:
        # Documents stay on this machine: a unix socket or loopback host only.
        match = re.match(r"^postgres(?:ql)?://(?:[^@/?]*@)?(\[[^\]]+\]|[^:/?]*)", url)
        if not match:
            raise ValueError("KE_DATABASE_URL must be a postgresql:// URL")
        host = match.group(1).strip("[]")
        if host not in LOOPBACK_HOSTS:
            raise ValueError("The database must be on loopback (localhost)")
        return url
