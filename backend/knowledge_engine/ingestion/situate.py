"""Contextual chunk summaries ("contextual retrieval"): one or two sentences that place a passage in
its document, written by the local model at indexing time and searched together with the passage.

A passage such as "Revenue grew 3% over the previous quarter" does not say whose revenue or which
quarter; the summary does, so both dense and full-text search can find it. The summary is search
text only: answers still quote and cite the passage itself.

The whole document comes first in the prompt and the passage last, so the model server can reuse
its prompt cache across the passages of one document.
"""

import hashlib
from typing import Any, Protocol

from knowledge_engine.ingestion.chunker import Chunk

PROMPT_VERSION = 1
DOCUMENT_CHARS = 12000  # longer documents are shown as a window around the passage
MAX_TOKENS = 256
SCHEMA = {
    "type": "object",
    "properties": {"context": {"type": "string", "maxLength": 500}},
    "required": ["context"],
}


class Generator(Protocol):
    async def generate(
        self,
        prompt: str,
        schema: dict[str, Any],
        on_start: Any = None,
        max_tokens: int | None = None,
        think: bool = False,
        model: str | None = None,
    ) -> dict[str, Any]: ...


class Cache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def put(self, key: str, payload: dict[str, Any]) -> None: ...


def window(document: str, chunk: Chunk) -> str:
    if len(document) <= DOCUMENT_CHARS:
        return document
    start = max(
        0, min((chunk.char_start or 0) - DOCUMENT_CHARS // 2, len(document) - DOCUMENT_CHARS)
    )
    return document[start : start + DOCUMENT_CHARS]


def prompt(title: str, document: str, chunk: Chunk) -> str:
    return (
        f"<document title={title!r}>\n{window(document, chunk)}\n</document>\n\n"
        "Here is a passage from that document:\n"
        f"<passage>\n{chunk.text}\n</passage>\n\n"
        "Write one or two short sentences that situate this passage within the document, to help "
        "search find it: say what and whom it is about and name the subject the passage leaves "
        "implicit (the organisation, product, person, policy or period). Use the document's own "
        "language and words. Do not repeat the passage, do not add facts that are not in the "
        'document. Return JSON: {"context": "..."}'
    )


async def situate(
    model: Generator, cache: Cache | None, model_name: str, title: str, document: str, chunk: Chunk
) -> str:
    text = prompt(title, document, chunk)
    key = hashlib.sha256(f"situate|{model_name}|p{PROMPT_VERSION}\n{text}".encode()).hexdigest()
    cached = cache.get(key) if cache is not None else None
    if cached is None:
        cached = await model.generate(text, SCHEMA, max_tokens=MAX_TOKENS, model=model_name)
        if cache is not None:
            cache.put(key, cached)
    return " ".join(str(cached.get("context", "")).split())[:500]
