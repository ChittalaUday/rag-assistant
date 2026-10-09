import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from knowledge_engine.llm.ollama import ModelClient

OnStart = Callable[[], Awaitable[None]]


class ModelGateway:
    """Serialises access to the single local model and bounds each call with a timeout."""

    def __init__(self, client: ModelClient, timeout: float):
        self.client = client
        self.timeout = timeout
        self.lock = asyncio.Lock()

    async def health(self) -> list[str]:
        return await self.client.health()

    async def embed(self, texts: list[str], on_start: OnStart | None = None) -> list[list[float]]:
        async with self.lock:
            if on_start:
                await on_start()
            return await asyncio.wait_for(self.client.embed(texts), timeout=self.timeout)

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
    ) -> dict[str, Any]:
        async with self.lock:
            if on_start:
                await on_start()
            call = self.client.generate(
                prompt, schema, max_tokens=max_tokens, think=think, model=model
            )
            return await asyncio.wait_for(call, timeout=self.timeout)
