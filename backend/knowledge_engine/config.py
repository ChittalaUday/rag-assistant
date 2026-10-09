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
    embedding_model: str = "embeddinggemma"
    # Vector size the Ollama embedding model produces. Changing the model or this size replaces
    # the vector column at start-up and re-embeds the documents.
    embedding_dimensions: int = 768
    # task prefixes the embedding model was trained with (these are embeddinggemma's)
    embedding_document_prefix: str = "title: none | text: "
    embedding_query_prefix: str = "task: search result | query: "
    # small talk when the nearest small-talk example is this much closer (cosine) than the nearest
    # question example (generation/smalltalk.py). Measured for embeddinggemma on 170 fixture
    # questions (highest 0.094) and 26 unseen small-talk messages (25 at 0.126 or more)
    small_talk_margin: float = 0.11
    model_timeout: float = 180
    job_timeout: float = 1800
    # ingestion
    max_upload_bytes: int = 20 * 1024 * 1024
    max_jobs: int = 32
    max_chunks: int = 600
    chunk_chars: int = Field(default=700, ge=100)  # smaller values cannot hold a sentence
    # "structure": cut at headings, then the last sentence end that fits; "hybrid": among the
    # sentence ends that fit, cut at the biggest topic change (sentence embeddings) and never
    # right before a sentence that refers back
    chunker: Literal["structure", "hybrid"] = "structure"
    # hard limit on a passage's embedding tokens; 0: the embedding model's own input window
    embedding_max_tokens: int = Field(default=0, ge=0)
    # one or two model-written sentences per chunk placing it in its document, searched with it
    contextual_chunks: bool = True
    context_model: str = ""  # Ollama model for those sentences; empty: the chat model
    # answering
    # Qwen3-Reranker model in Ollama that rescores the candidates (empty: feature ranking only)
    rerank_model: str = "dengcao/Qwen3-Reranker-0.6B:F16"
    rerank_weight: float = Field(default=0.7, ge=0, le=1)  # its share of the final score
    context_chars: int = 9000  # evidence budget in the answer prompt
    rewrite_followups: bool = True  # resolve "she"/"that" with one small model call
    # match every answer sentence to the passages that state it (one extra model call): completes
    # citations and shows which statements no passage supports
    verify_citations: bool = True
    answer_thinking: bool = False  # let the answer model reason before answering (slower)
    # follow names in the best passage to other documents (append-only second retrieval round)
    second_hop: Literal["off", "relationship", "always"] = "always"
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
