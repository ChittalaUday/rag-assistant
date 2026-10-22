import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from knowledge_engine.llm.answer_choice import CHOSEN
from knowledge_engine.llm.ollama import ModelClient, OnText

OnStart = Callable[[], Awaitable[None]]


class ModelGateway:
    """Serialises access to the single local model and bounds each call with a timeout."""

    def __init__(self, client: ModelClient, timeout: float, load_timeout: float | None = None):
        self.client = client
        self.timeout = timeout
        self.load_timeout = load_timeout or timeout
        self.lock = asyncio.Lock()

    async def health(self) -> list[str]:
        return await self.client.health()

    async def loaded(self) -> list[dict[str, Any]]:
        return await self.client.loaded()

    async def load(self, model: str, embedding: bool = False) -> None:
        """Warm a model. Not serialised with generation: loading only fills memory, and the UI
        must be able to report progress while an answer is being produced."""
        await asyncio.wait_for(self.client.load(model, embedding), timeout=self.load_timeout)

    async def chat_state(self) -> dict[str, Any] | None:
        return await asyncio.wait_for(self.client.chat_state(), timeout=15)

    async def server_models(self) -> list[str]:
        return await asyncio.wait_for(self.client.server_models(), timeout=15)

    async def embed(self, texts: list[str], on_start: OnStart | None = None) -> list[list[float]]:
        async with self.lock:
            if on_start:
                await on_start()
            return await asyncio.wait_for(self.client.embed(texts), timeout=self.timeout)

    async def count_tokens(self, text: str) -> int:
        async with self.lock:
            return await asyncio.wait_for(self.client.count_tokens(text), timeout=self.timeout)

    async def embedding_context(self) -> int:
        return await asyncio.wait_for(self.client.embedding_context(), timeout=self.timeout)

    async def relevance(self, model: str, query: str, documents: list[str]) -> list[float]:
        async with self.lock:
            return await asyncio.wait_for(
                self.client.relevance(model, query, documents), timeout=self.timeout
            )

    async def generate(
        self,
        prompt: str,
        schema: dict[str, Any],
        on_start: OnStart | None = None,
        max_tokens: int | None = None,
        think: bool = False,
        model: str | None = None,
        on_text: OnText | None = None,
    ) -> dict[str, Any]:
        async with self.lock:
            if on_start:
                await on_start()
            chosen = CHOSEN.get()  # the answer model picked for this question, if any
            model = model or (chosen[1] if chosen else None)
            streaming = {"on_text": on_text} if on_text else {}
            call = self.client.generate(
                prompt, schema, max_tokens=max_tokens, think=think, model=model, **streaming
            )
            return await asyncio.wait_for(call, timeout=self.timeout)
