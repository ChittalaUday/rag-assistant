"""The answer model on an OpenAI-compatible server (vLLM), optionally behind model-sleeper.

`ServerChat` speaks the server's chat API (structured output through a JSON schema, streaming) and
the sleeper's control API (is the model asleep, loading or ready, and wake it). `Routed` sends the
answer model's calls there and everything else (embeddings, reranking, other models) to Ollama.
"""

import asyncio
import json
import time
from typing import Any

import httpx

from knowledge_engine.config import Settings
from knowledge_engine.llm.answer_choice import CHOSEN
from knowledge_engine.llm.ollama import (
    NUM_PREDICT,
    THINKING_NUM_PREDICT,
    ModelClient,
    OnText,
    prompt_budget_bytes,
    record_usage,
)

POLL_SECONDS = 2.0


def response_format(schema: dict[str, Any]) -> dict[str, Any]:
    """Strict schema decoding makes the model leave out "\\n" escapes (answers lose their line
    breaks or stop after the heading), so a schema with long free text asks for plain JSON instead;
    the prompt describes the fields and the caller validates them."""
    long_text = any(
        p.get("type") == "string" and p.get("maxLength", 0) >= 1000
        for p in schema.get("properties", {}).values()
    )
    if long_text:
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {"name": "answer", "schema": schema, "strict": True},
    }


class ServerChat:
    def __init__(self, settings: Settings):
        self.s = settings
        self.headers = (
            {"Authorization": f"Bearer {settings.chat_api_key}"} if settings.chat_api_key else {}
        )
        self._waking: asyncio.Task[None] | None = None

    async def generate(
        self,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
        think: bool = False,
        model: str | None = None,
        on_text: OnText | None = None,
    ) -> dict[str, Any]:
        """Structured output for the prompt (same contract as Ollama.generate)."""
        num_predict = max_tokens or (THINKING_NUM_PREDICT if think else NUM_PREDICT)
        if len(prompt.encode()) > prompt_budget_bytes(schema, num_predict):
            raise ValueError("Prompt exceeds the conservative model context budget")
        body: dict[str, Any] = {
            "model": model or self.s.chat_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": num_predict,
            "stream": on_text is not None,
            "response_format": response_format(schema),
            "chat_template_kwargs": {"enable_thinking": think},
        }
        url = self.s.chat_url + "/v1/chat/completions"
        started = time.monotonic()
        usage: dict[str, Any] = {}
        async with httpx.AsyncClient(timeout=self.s.model_timeout) as c:
            if on_text is None:
                r = await c.post(url, json=body, headers=self.headers)
                r.raise_for_status()
                reply = r.json()
                content = reply["choices"][0]["message"]["content"] or ""
                usage = reply.get("usage") or {}
            else:
                body["stream_options"] = {"include_usage": True}
                content = ""
                async with c.stream("POST", url, json=body, headers=self.headers) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                            continue
                        part = json.loads(line[5:])
                        if part.get("error"):
                            raise ValueError(f"Model error: {part['error']}")
                        usage = part.get("usage") or usage
                        for choice in part.get("choices", []):
                            piece = (choice.get("delta") or {}).get("content") or ""
                            if piece:
                                content += piece
                                await on_text(content)
        record_usage(
            "generate",
            {
                "prompt_eval_count": usage.get("prompt_tokens", 0),
                "eval_count": usage.get("completion_tokens", 0),
                "total_duration": int((time.monotonic() - started) * 1e9),
            },
        )
        value: dict[str, Any] = json.loads(content)
        return value

    def _control(self, path: str) -> str:
        return f"{self.s.chat_control_url}{path}"

    @property
    def _control_headers(self) -> dict[str, str]:
        token = self.s.chat_control_token
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def state(self) -> dict[str, Any]:
        """{"state": asleep | starting | ready | unreachable, "loading_seconds", "detail"}."""
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                if self.s.chat_control_url and self.s.chat_control_name:
                    r = await c.get(self._control("/status"), headers=self._control_headers)
                    r.raise_for_status()
                    entry = r.json().get(self.s.chat_control_name)
                    if entry is None:
                        return {
                            "state": "unreachable",
                            "detail": f"the sleeper does not know {self.s.chat_control_name}",
                        }
                    return {
                        "state": entry["state"],
                        "loading_seconds": entry.get("starting_seconds"),
                        "detail": None,
                    }
                r = await c.get(self.s.chat_url + "/v1/models", headers=self.headers)  # no sleeper
                return {"state": "ready" if r.status_code == 200 else "unreachable", "detail": None}
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            return {"state": "unreachable", "detail": f"model server unreachable: {exc}"}

    async def models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(self.s.chat_url + "/v1/models", headers=self.headers)
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", [])]

    async def wake(self) -> None:
        """Bring the model into memory and wait until it answers (minutes after a sleep)."""
        deadline = time.monotonic() + self.s.model_load_timeout
        woke = False
        while time.monotonic() < deadline:
            state = (await self.state())["state"]
            if state == "ready":
                return
            if state == "unreachable":
                raise RuntimeError(f"model server unreachable at {self.s.chat_url}")
            if not woke and self.s.chat_control_url:
                woke = True  # the sleeper's wake call blocks until ready: leave it running
                self._waking = asyncio.create_task(self._post_wake())
            await asyncio.sleep(POLL_SECONDS)
        raise TimeoutError("the model did not finish loading in time")

    async def _post_wake(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=self.s.model_load_timeout) as c:
                await c.post(
                    self._control(f"/wake/{self.s.chat_control_name}"),
                    headers=self._control_headers,
                )
        except httpx.HTTPError:
            pass  # wake() keeps polling and reports what it sees


class Routed:
    """Answer-model calls go to the server; embeddings, reranking and other models to `local`."""

    def __init__(self, local: ModelClient, chat: ServerChat, settings: Settings):
        self.local = local
        self.chat = chat
        self.s = settings

    def _on_server(self, model: str | None) -> bool:
        chosen = CHOSEN.get()
        if chosen and model == chosen[1]:
            return chosen[0] == "vllm"
        return model is None or model == self.s.chat_model

    async def health(self) -> list[str]:
        return await self.local.health()

    async def loaded(self) -> list[dict[str, Any]]:
        return await self.local.loaded()

    async def load(self, model: str, embedding: bool = False) -> None:
        if not embedding and self._on_server(model):
            await self.chat.wake()
        else:
            await self.local.load(model, embedding)

    async def chat_state(self) -> dict[str, Any] | None:
        return await self.chat.state()

    async def server_models(self) -> list[str]:
        try:
            return await self.chat.models()
        except (httpx.HTTPError, ValueError, KeyError):
            return []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self.local.embed(texts)

    async def count_tokens(self, text: str) -> int:
        return await self.local.count_tokens(text)

    async def embedding_context(self) -> int:
        return await self.local.embedding_context()

    async def relevance(self, model: str, query: str, documents: list[str]) -> list[float]:
        return await self.local.relevance(model, query, documents)

    async def generate(
        self,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
        think: bool = False,
        model: str | None = None,
        on_text: OnText | None = None,
    ) -> dict[str, Any]:
        target = self.chat if self._on_server(model) else self.local
        return await target.generate(
            prompt, schema, max_tokens=max_tokens, think=think, model=model, on_text=on_text
        )
