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
