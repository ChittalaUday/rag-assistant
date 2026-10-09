import asyncio
import json
import math
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from knowledge_engine.config import Settings

OnText = Callable[[str], Awaitable[None]]  # receives the output so far, as it is generated
NUM_CTX = 16384
NUM_PREDICT = 2048
THINKING_NUM_PREDICT = 6144  # reasoning tokens come out of the same output budget
# UTF-8 bytes conservatively upper-bound tokens (English averages ~4 bytes/token, CJK ~2).
BYTES_PER_TOKEN = 2


def prompt_budget_bytes(schema: dict[str, Any], max_tokens: int = NUM_PREDICT) -> int:
    """Largest prompt (in UTF-8 bytes) that still leaves room for the schema and the output."""
    schema_bytes = len(json.dumps(schema).encode())
    return (NUM_CTX - max_tokens) * BYTES_PER_TOKEN - schema_bytes


# Qwen3-Reranker prompt: the model answers "yes" or "no"; relevance is P(yes) / (P(yes) + P(no)).
RERANK_TEMPLATE = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and "
    'the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n'
    "<|im_start|>user\n<Instruct>: Given a question, retrieve passages that answer it\n"
    "<Query>: {query}\n<Document>: {document}<|im_end|>\n"
    "<|im_start|>assistant\n<think>\n\n</think>\n\n"
)
RERANK_PARALLEL = 4


def yes_probability(top: list[dict[str, Any]]) -> float:
    """P(yes) normalised against P(no) over the first token's alternatives (any casing)."""
    mass = {"yes": 0.0, "no": 0.0}
    for t in top:
        word = str(t.get("token", "")).strip().casefold()
        if word in mass:
            mass[word] += math.exp(float(t["logprob"]))
    total = mass["yes"] + mass["no"]
    return mass["yes"] / total if total else 0.0


class ModelClient(Protocol):
    async def health(self) -> list[str]: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def relevance(self, model: str, query: str, documents: list[str]) -> list[float]: ...
    async def count_tokens(self, text: str) -> int: ...
    async def embedding_context(self) -> int: ...
    async def generate(
        self,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
        think: bool = False,
        model: str | None = None,
        on_text: OnText | None = None,
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

    async def count_tokens(self, text: str) -> int:
        """Tokens the embedding model reads for `text` (its own tokenizer, via the embed call)."""
        async with httpx.AsyncClient(timeout=self.s.model_timeout) as c:
            r = await c.post(
                self.s.ollama_url + "/api/embed",
                json={"model": self.s.embedding_model, "input": [text], "truncate": False},
            )
            if r.status_code == 400:  # longer than the model's input window
                return 1 << 20
            r.raise_for_status()
            return int(r.json().get("prompt_eval_count", 0))

    async def embedding_context(self) -> int:
        """The embedding model's input window in tokens, as the model reports it."""
        async with httpx.AsyncClient(timeout=self.s.model_timeout) as c:
            r = await c.post(
                self.s.ollama_url + "/api/show", json={"model": self.s.embedding_model}
            )
            r.raise_for_status()
            info = r.json().get("model_info", {})
            sizes = [int(v) for k, v in info.items() if k.endswith(".context_length")]
            return min(sizes) if sizes else 512

    async def relevance(self, model: str, query: str, documents: list[str]) -> list[float]:
        """Score each document's relevance to the query with a Qwen3-Reranker model (0..1)."""
        limit = asyncio.Semaphore(RERANK_PARALLEL)
        async with httpx.AsyncClient(timeout=self.s.model_timeout) as c:

            async def one(document: str) -> float:
                async with limit:
                    r = await c.post(
                        self.s.ollama_url + "/api/generate",
                        json={
                            "model": model,
                            "prompt": RERANK_TEMPLATE.format(query=query, document=document),
                            "raw": True,
                            "stream": False,
                            "logprobs": True,
                            "top_logprobs": 20,
                            "options": {"num_predict": 1, "temperature": 0},
                        },
                    )
                    r.raise_for_status()
                    tokens = r.json().get("logprobs") or []
                    return yes_probability(tokens[0].get("top_logprobs", [])) if tokens else 0.0

            return list(await asyncio.gather(*(one(d) for d in documents)))

    async def generate(
        self,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
        think: bool = False,
        model: str | None = None,
        on_text: OnText | None = None,
    ) -> dict[str, Any]:
        """Structured output for the prompt. With `on_text`, the output is streamed and the text
        so far is passed on as it grows (thinking tokens are not)."""
        num_predict = max_tokens or (THINKING_NUM_PREDICT if think else NUM_PREDICT)
        if len(prompt.encode()) > prompt_budget_bytes(schema, num_predict):
            raise ValueError("Prompt exceeds the conservative model context budget")
        body = {
            "model": model or self.s.chat_model,
            "stream": on_text is not None,
            "think": think,
            "format": schema,
            "options": {"temperature": 0, "num_ctx": NUM_CTX, "num_predict": num_predict},
            "messages": [{"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=self.s.model_timeout) as c:
            if on_text is None:
                r = await c.post(self.s.ollama_url + "/api/chat", json=body)
                r.raise_for_status()
                content = r.json()["message"]["content"]
            else:
                content = ""
                async with c.stream("POST", self.s.ollama_url + "/api/chat", json=body) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        part = json.loads(line)
                        if part.get("error"):
                            raise ValueError(f"Model error: {part['error']}")
                        piece = part.get("message", {}).get("content", "")
                        if piece:
                            content += piece
                            await on_text(content)
            value: dict[str, Any] = json.loads(content)
            return value
