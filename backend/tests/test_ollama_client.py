import json as json_module

import httpx
import pytest
from knowledge_engine.config import Settings
from knowledge_engine.llm import ollama
from knowledge_engine.llm.ollama import Ollama, prompt_budget_bytes


class FakeClient:
    """Stands in for httpx.AsyncClient; records requests and answers from a script."""

    calls: list[dict] = []
    script: list[tuple[int, dict]] = []

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def post(self, url, json):
        FakeClient.calls.append({"url": url, **json})
        status, body = FakeClient.script.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    def stream(self, method, url, json):
        """Streams the scripted body as newline-delimited JSON, the way Ollama streams."""
        FakeClient.calls.append({"url": url, **json})
        status, parts = FakeClient.script.pop(0)
        lines = "\n".join(json_module.dumps(p) for p in parts).encode()
        response = httpx.Response(status, content=lines, request=httpx.Request(method, url))

        class Streamed:
            async def __aenter__(self):
                return response

            async def __aexit__(self, *exc):
                return None

        return Streamed()


@pytest.fixture(autouse=True)
def fake_http(monkeypatch):
    FakeClient.calls, FakeClient.script = [], []
    monkeypatch.setattr(ollama.httpx, "AsyncClient", FakeClient)


async def test_embedding_overflow_is_retried_with_truncation_instead_of_failing():
    FakeClient.script = [
        (400, {"error": "input length exceeds context"}),
        (200, {"embeddings": [[0.1]]}),
    ]
    assert await Ollama(Settings()).embed(["dense table text"]) == [[0.1]]
    assert [c["truncate"] for c in FakeClient.calls] == [False, True]


async def test_other_embedding_errors_still_raise_and_good_batches_are_not_retried():
    FakeClient.script = [(500, {"error": "boom"})]
    with pytest.raises(httpx.HTTPStatusError):
        await Ollama(Settings()).embed(["x"])
    FakeClient.calls, FakeClient.script = [], [(200, {"embeddings": [[1.0], [2.0]]})]
    assert len(await Ollama(Settings()).embed(["a", "b"])) == 2 and len(FakeClient.calls) == 1


async def test_generation_budget_scales_with_the_requested_output_size():
    schema = {"type": "object"}
    assert prompt_budget_bytes(schema, 4096) < prompt_budget_bytes(schema)
    with pytest.raises(ValueError, match="context budget"):
        await Ollama(Settings()).generate("x" * 30000, schema)
    FakeClient.script = [(200, {"message": {"content": '{"ok": true}'}})]
    assert await Ollama(Settings()).generate("hi", schema, max_tokens=3000) == {"ok": True}
    assert FakeClient.calls[0]["options"]["num_predict"] == 3000


def test_ollama_must_be_on_loopback():
    with pytest.raises(ValueError, match="loopback"):
        Ollama(Settings(ollama_url="http://example.com:11434"))


async def test_generation_streams_the_text_so_far_when_asked_and_skips_thinking():
    FakeClient.script = [
        (
            200,
            [
                {"message": {"thinking": "hmm", "content": ""}},
                {"message": {"content": '{"answer": "Mi'}},
                {"message": {"content": 'ra"}'}},
                {"done": True},
            ],
        )
    ]
    seen: list[str] = []

    async def on_text(text: str) -> None:
        seen.append(text)

    value = await Ollama(Settings()).generate("q", {"type": "object"}, on_text=on_text)
    assert value == {"answer": "Mira"}
    assert seen == ['{"answer": "Mi', '{"answer": "Mira"}']
    assert FakeClient.calls[0]["stream"] is True


async def test_a_model_error_mid_stream_fails_loudly():
    FakeClient.script = [(200, [{"message": {"content": "{"}}, {"error": "out of memory"}])]

    async def on_text(text: str) -> None:
        pass

    with pytest.raises(ValueError, match="out of memory"):
        await Ollama(Settings()).generate("q", {"type": "object"}, on_text=on_text)


async def test_model_usage_is_counted_for_the_request_being_answered():
    FakeClient.script = [
        (200, [{"message": {"content": '{"a": 1}'}}, {"done": True, "prompt_eval_count": 120,
               "eval_count": 7, "total_duration": 2_500_000_000, "load_duration": 500_000_000}]),
        (200, {"embeddings": [[0.1]], "prompt_eval_count": 4, "total_duration": 30_000_000}),
        (200, {"embeddings": [[0.1]]}),
    ]  # fmt: skip
    usage: dict = {}
    token = ollama.USAGE.set(usage)

    async def on_text(text: str) -> None:
        pass

    try:
        await Ollama(Settings()).generate("q", {"type": "object"}, on_text=on_text)
        await Ollama(Settings()).embed(["x"])
    finally:
        ollama.USAGE.reset(token)
    assert usage == {
        "generate": {
            "calls": 1,
            "prompt_tokens": 120,
            "output_tokens": 7,
            "model_ms": 2500,
            "load_ms": 500,
        },
        "embed": {"calls": 1, "prompt_tokens": 4, "output_tokens": 0, "model_ms": 30, "load_ms": 0},
    }
    await Ollama(Settings()).embed(["outside a request"])  # not counted
    assert usage["embed"]["calls"] == 1


async def test_loading_a_model_sends_no_prompt_and_keeps_it_in_memory():
    FakeClient.script = [(200, {}), (200, {"embeddings": [[0.1]]})]
    client = Ollama(Settings(model_keep_alive="45m"))
    await client.load("qwen3.5:9b")
    await client.load("embeddinggemma", embedding=True)
    chat, embed = FakeClient.calls
    assert chat["url"].endswith("/api/generate") and chat["prompt"] == "" and not chat["stream"]
    assert embed["url"].endswith("/api/embed") and embed["input"] == ["load"]
    assert chat["keep_alive"] == embed["keep_alive"] == "45m"
