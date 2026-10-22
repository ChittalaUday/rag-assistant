import json as json_module

import httpx
import pytest
from fakes import FakeModel
from fastapi.testclient import TestClient
from knowledge_engine.app import create_app
from knowledge_engine.config import Settings
from knowledge_engine.llm import server
from knowledge_engine.llm.ollama import USAGE
from knowledge_engine.llm.server import Routed, ServerChat

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


def settings(**more):
    return Settings(
        chat_url="http://spark:8008",
        chat_model="nvidia/Qwen3.6-35B-A3B-NVFP4",
        chat_control_url="http://spark:8099",
        chat_control_name="qwen36",
        chat_control_token="secret",
        **more,
    )


class FakeHttp:
    """Stands in for httpx.AsyncClient: scripted answers by URL; records what was sent."""

    calls: list[dict] = []
    routes: dict[str, object] = {}

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def _reply(self, method, url, json=None, headers=None):
        FakeHttp.calls.append({"method": method, "url": url, "json": json, "headers": headers})
        status, body = FakeHttp.routes[url]
        if isinstance(body, Exception):
            raise body
        request = httpx.Request(method, url)
        if isinstance(body, str):  # an SSE body
            return httpx.Response(status, content=body.encode(), request=request)
        return httpx.Response(status, json=body, request=request)

    async def get(self, url, headers=None):
        return self._reply("GET", url, headers=headers)

    async def post(self, url, json=None, headers=None):
        return self._reply("POST", url, json=json, headers=headers)

    def stream(self, method, url, json=None, headers=None):
        response = self._reply(method, url, json=json, headers=headers)

        class Streamed:
            async def __aenter__(self):
                return response

            async def __aexit__(self, *exc):
                return None

        return Streamed()


@pytest.fixture(autouse=True)
def fake_http(monkeypatch):
    FakeHttp.calls, FakeHttp.routes = [], {}
    monkeypatch.setattr(server.httpx, "AsyncClient", FakeHttp)


CHAT = "http://spark:8008/v1/chat/completions"
STATUS = "http://spark:8099/status"


async def test_generate_asks_the_server_for_schema_constrained_json_and_counts_tokens():
    reply = {
        "choices": [{"message": {"content": '{"answer": "Mira"}'}}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 5},
    }
    FakeHttp.routes = {CHAT: (200, reply)}
    USAGE.set({})
    value = await ServerChat(settings()).generate("Who?", SCHEMA)
    assert value == {"answer": "Mira"}
    body = FakeHttp.calls[0]["json"]
    assert body["model"] == "nvidia/Qwen3.6-35B-A3B-NVFP4" and body["temperature"] == 0
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["chat_template_kwargs"] == {"enable_thinking": False} and not body["stream"]
    usage = USAGE.get()["generate"]
    assert (usage["calls"], usage["prompt_tokens"], usage["output_tokens"]) == (1, 40, 5)


async def test_generate_streams_the_text_so_far():
    chunks = ['{"answer":', ' "Mi', 'ra"}']
    events = [{"choices": [{"delta": {"content": c}}]} for c in chunks]
    events.append({"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}})
    sse = "".join(f"data: {json_module.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    FakeHttp.routes = {CHAT: (200, sse)}
    seen = []

    async def on_text(text):
        seen.append(text)

    value = await ServerChat(settings(chat_api_key="k")).generate("Who?", SCHEMA, on_text=on_text)
    assert value == {"answer": "Mira"}
    assert seen == ['{"answer":', '{"answer": "Mi', '{"answer": "Mira"}']
    call = FakeHttp.calls[0]
    assert call["json"]["stream"] and call["headers"] == {"Authorization": "Bearer k"}


async def test_an_oversized_prompt_is_refused_before_any_request():
    with pytest.raises(ValueError, match="context budget"):
        await ServerChat(settings()).generate("x" * 100_000, SCHEMA)
    assert FakeHttp.calls == []


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"state": "asleep", "starting_seconds": None}, ("asleep", None)),
        ({"state": "starting", "starting_seconds": 42}, ("starting", 42)),
        ({"state": "ready", "starting_seconds": None}, ("ready", None)),
    ],
)
async def test_state_comes_from_the_sleeper(entry, expected):
    FakeHttp.routes = {STATUS: (200, {"qwen36": entry})}
    found = await ServerChat(settings()).state()
    assert (found["state"], found["loading_seconds"]) == expected
    assert FakeHttp.calls[0]["headers"] == {"Authorization": "Bearer secret"}


async def test_state_reports_an_unreachable_or_unknown_server():
    FakeHttp.routes = {STATUS: (200, {"other": {"state": "ready"}})}
    assert (await ServerChat(settings()).state())["state"] == "unreachable"
    FakeHttp.routes = {STATUS: (200, httpx.ConnectError("refused"))}
    found = await ServerChat(settings()).state()
    assert found["state"] == "unreachable" and "refused" in found["detail"]


async def test_without_a_sleeper_the_server_itself_is_probed():
    FakeHttp.routes = {"http://spark:8008/v1/models": (200, {"data": []})}
    chat = ServerChat(Settings(chat_url="http://spark:8008"))
    assert (await chat.state())["state"] == "ready"


async def test_wake_polls_until_ready_and_asks_the_sleeper_once(monkeypatch):
    states = iter(["asleep", "starting", "starting", "ready"])
    chat = ServerChat(settings())

    async def state():
        return {"state": next(states)}

    monkeypatch.setattr(chat, "state", state)
    monkeypatch.setattr(server, "POLL_SECONDS", 0)
    FakeHttp.routes = {"http://spark:8099/wake/qwen36": (200, {"ready": True})}
    await chat.wake()
    wakes = [c for c in FakeHttp.calls if c["url"].endswith("/wake/qwen36")]
    assert len(wakes) == 1 and wakes[0]["headers"] == {"Authorization": "Bearer secret"}


async def test_wake_fails_when_the_server_cannot_be_reached(monkeypatch):
    chat = ServerChat(settings())

    async def state():
        return {"state": "unreachable"}

    monkeypatch.setattr(chat, "state", state)
    with pytest.raises(RuntimeError, match="unreachable"):
        await chat.wake()


class Remote:
    """Records which side a call went to."""

    def __init__(self):
        self.calls = []

    async def generate(
        self, prompt, schema, max_tokens=None, think=False, model=None, on_text=None
    ):
        self.calls.append(("generate", model))
        return {"answer": "x"}

    async def wake(self):
        self.calls.append(("wake",))

    async def state(self):
        return {"state": "ready"}


class Local(FakeModel):
    def __init__(self):
        self.calls = []

    async def generate(
        self, prompt, schema, max_tokens=None, think=False, model=None, on_text=None
    ):
        self.calls.append(("generate", model))
        return {"answer": "local"}

    async def load(self, model, embedding=False):
        self.calls.append(("load", model, embedding))


async def test_routing_sends_the_answer_model_to_the_server_and_the_rest_to_ollama():
    s = settings()
    local, remote = Local(), Remote()
    routed = Routed(local, remote, s)
    assert await routed.generate("p", SCHEMA) == {"answer": "x"}
    assert await routed.generate("p", SCHEMA, model=s.chat_model) == {"answer": "x"}
    assert await routed.generate("p", SCHEMA, model="gemma3:4b") == {"answer": "local"}
    await routed.load(s.chat_model)
    await routed.load("embeddinggemma", embedding=True)
    assert remote.calls == [("generate", None), ("generate", s.chat_model), ("wake",)]
    assert local.calls == [("generate", "gemma3:4b"), ("load", "embeddinggemma", True)]
    assert (await routed.chat_state())["state"] == "ready"
    assert await routed.embed(["a"]) and await routed.health()  # embeddings stay local


def client(tmp_path, model, **more):
    return TestClient(create_app(Settings(data_dir=tmp_path, **more), model))


def test_status_shows_the_server_model_asleep_loading_and_loaded(tmp_path):
    model = FakeModel()
    model.server_state = {"state": "asleep"}
    with client(tmp_path, model, chat_url="http://spark:8008", chat_model="qwen-on-server") as c:

        def chat():
            return next(
                m for m in c.get("/api/models/status").json()["models"] if m["role"] == "chat"
            )

        assert (chat()["state"], chat()["where"], chat()["loaded"]) == ("asleep", "server", False)
        model.server_state = {"state": "starting", "loading_seconds": 31}
        assert (chat()["state"], chat()["loading_seconds"]) == ("loading", 31)
        model.server_state = {"state": "ready"}
        assert (chat()["state"], chat()["loaded"]) == ("loaded", True)
        roles = [m["role"] for m in c.get("/api/models/status").json()["models"]]
        assert roles == ["chat", "embedding"]  # the local Ollama models are still listed


def test_loading_wakes_the_server_model_and_an_unreachable_server_is_503(tmp_path):
    model = FakeModel()
    model.server_state = {"state": "asleep"}
    with client(tmp_path, model, chat_url="http://spark:8008", chat_model="qwen-on-server") as c:
        body = c.post("/api/models/load", json={"roles": ["chat"]}).json()
        assert model.load_calls == [("qwen-on-server", False)]
        assert body["models"][0]["state"] == "loaded"
        model.server_state = {"state": "unreachable", "detail": "model server unreachable: down"}
        reply = c.post("/api/models/load", json={"roles": ["chat"]})
        assert reply.status_code == 503 and "down" in reply.json()["detail"]


def test_health_does_not_look_for_the_server_model_in_ollama(tmp_path):
    with client(
        tmp_path, FakeModel(), chat_url="http://spark:8008", chat_model="qwen-on-server"
    ) as c:
        warnings = c.get("/api/health").json()["warnings"]
        assert not any("Model missing" in w for w in warnings)


def test_a_server_url_must_be_http():
    with pytest.raises(ValueError, match="http"):
        Settings(chat_url="ftp://spark")


def test_long_free_text_is_not_decoded_with_a_strict_schema():
    from knowledge_engine.llm.server import response_format

    answer = {"properties": {"answer": {"type": "string", "maxLength": 12000}}}
    short = {"properties": {"question": {"type": "string", "maxLength": 300}}}
    assert response_format(answer) == {"type": "json_object"}
    assert response_format(short)["type"] == "json_schema"


def test_every_answer_model_of_the_server_and_ollama_is_listed(tmp_path):
    model = FakeModel()
    model.server_state = {"state": "ready"}
    model.server_ids = ("qwen-on-server", "other-on-server")
    with client(tmp_path, model, chat_url="http://spark:8008", chat_model="qwen-on-server") as c:
        listed = c.get("/api/models/answer").json()["models"]
        assert [(m["provider"], m["model"], m["default"]) for m in listed] == [
            ("vllm", "qwen-on-server", True),
            ("vllm", "other-on-server", False),
            ("ollama", "qwen3.5:9b", False),  # the embedding model is not an answer model
        ]
        model.server_state = {"state": "unreachable", "detail": "down"}
        reply = c.get("/api/models/answer").json()
        assert [m["provider"] for m in reply["models"]] == ["vllm", "ollama"]  # still listed


def test_a_question_can_choose_an_ollama_model_and_unknown_models_are_refused(tmp_path):
    model = FakeModel()
    model.server_state = {"state": "ready"}
    with client(tmp_path, model, chat_url="http://spark:8008", chat_model="qwen-on-server") as c:
        status = c.get(
            "/api/models/status", params={"answer_provider": "ollama", "answer_model": "qwen3.5:9b"}
        ).json()["models"]
        assert [(m["role"], m["model"], m["where"]) for m in status][:1] == [
            ("chat", "qwen3.5:9b", "local")
        ]
        pick = {"answer_provider": "ollama", "answer_model": "qwen3.5:9b"}
        assert c.post("/api/chat", json={"question": "Hello", **pick}).status_code == 200
        bad = c.post(
            "/api/chat",
            json={"question": "Hello", "answer_provider": "ollama", "answer_model": "nope:1b"},
        )
        assert bad.status_code == 422
        assert c.post("/api/chat", json={"question": "Hi", "answer_model": "x"}).status_code == 422


async def test_a_chosen_model_is_routed_to_its_backend():
    from knowledge_engine.llm.answer_choice import CHOSEN

    s = settings()
    local, remote = Local(), Remote()
    routed = Routed(local, remote, s)
    CHOSEN.set(("ollama", "qwen3.5:9b"))
    await routed.generate("p", SCHEMA, model="qwen3.5:9b")
    CHOSEN.set(("vllm", "other-on-server"))
    await routed.generate("p", SCHEMA, model="other-on-server")
    CHOSEN.set(None)
    assert local.calls == [("generate", "qwen3.5:9b")]
    assert remote.calls == [("generate", "other-on-server")]
