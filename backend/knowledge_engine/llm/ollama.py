import json
import re
from typing import Any, Protocol

import httpx

from knowledge_engine.config import Settings

NUM_CTX = 12288
NUM_PREDICT = 2048
EXTRACTION_NUM_PREDICT = 3584  # dense passages can list 40 entities and 40 relations
# UTF-8 bytes conservatively upper-bound tokens (English averages ~4 bytes/token, CJK ~2).
BYTES_PER_TOKEN = 2


def prompt_budget_bytes(schema: dict[str, Any], max_tokens: int = NUM_PREDICT) -> int:
    """Largest prompt (in UTF-8 bytes) that still leaves room for the schema and the output."""
    schema_bytes = len(json.dumps(schema).encode())
    return (NUM_CTX - max_tokens) * BYTES_PER_TOKEN - schema_bytes


class ModelClient(Protocol):
    async def health(self) -> list[str]: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def generate(
        self, prompt: str, schema: dict[str, Any], max_tokens: int | None = None
    ) -> dict[str, Any]: ...


class Ollama:
    def __init__(self, settings: Settings):
        self.s = settings
        if not re.match(r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?/?$", settings.ollama_url):
            raise ValueError("Ollama must use a loopback HTTP URL")

    async def health(self) -> list[str]:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(self.s.ollama_url + "/api/tags")
            r.raise_for_status()
            return [m["name"] for m in r.json()["models"]]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts. Token-dense text (tables, code) can exceed the model window; rather than
        failing the document, the batch is retried with truncation.
        ponytail: a truncated chunk is embedded from its first ~256 tokens only; its full text
        stays searchable through the full-text index."""
        async with httpx.AsyncClient(timeout=self.s.model_timeout) as c:
            for truncate in (False, True):
                r = await c.post(
                    self.s.ollama_url + "/api/embed",
                    json={"model": self.s.embedding_model, "input": texts, "truncate": truncate},
                )
                if r.status_code == 400 and not truncate:
                    continue
                r.raise_for_status()
                result: list[list[float]] = r.json()["embeddings"]
                return result
        raise AssertionError("unreachable")  # pragma: no cover

    async def generate(
        self, prompt: str, schema: dict[str, Any], max_tokens: int | None = None
    ) -> dict[str, Any]:
        num_predict = max_tokens or NUM_PREDICT
        if len(prompt.encode()) > prompt_budget_bytes(schema, num_predict):
            raise ValueError("Prompt exceeds the conservative model context budget")
        async with httpx.AsyncClient(timeout=self.s.model_timeout) as c:
            r = await c.post(
                self.s.ollama_url + "/api/chat",
                json={
                    "model": self.s.chat_model,
                    "stream": False,
                    "think": False,
                    "format": schema,
                    "options": {"temperature": 0, "num_ctx": NUM_CTX, "num_predict": num_predict},
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            r.raise_for_status()
            value: dict[str, Any] = json.loads(r.json()["message"]["content"])
            return value
