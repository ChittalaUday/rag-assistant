from typing import Literal

from pydantic import BaseModel

Role = Literal["chat", "embedding", "rerank", "context"]
# loaded: in memory, ready; loading: being brought into memory now; asleep: stopped to free memory,
# wakes on demand (a model server behind model-sleeper); not_loaded: installed but not in memory
State = Literal["loaded", "loading", "asleep", "not_loaded", "not_installed", "unreachable"]


class ModelState(BaseModel):
    role: Role  # chat: writes answers; embedding: search; rerank: rescoring; context: indexing text
    model: str
    installed: bool
    loaded: bool  # held in memory now, so the next call starts without a load delay
    size_bytes: int | None = None  # memory it takes while loaded
    expires_at: str | None = None  # when Ollama will unload it if unused
    state: State = "not_loaded"
    where: Literal["local", "server"] = "local"  # server: a remote model server, not local Ollama
    detail: str | None = None
    loading_seconds: int | None = None  # how long the current load has taken


class ModelsStatus(BaseModel):
    ollama_available: bool
    models: list[ModelState]
    error: str | None = None


class LoadRequest(BaseModel):
    roles: list[Role] | None = None  # None: every configured model
    answer_provider: Literal["vllm", "ollama"] | None = None  # the answer model to use; both or
    answer_model: str | None = None  # neither (None: the default)


class AnswerModel(BaseModel):
    provider: Literal["vllm", "ollama"]  # vllm: the model server; ollama: local
    model: str
    state: State  # loaded / asleep / not_loaded / unreachable ...
    default: bool = False  # what is used when a question names no model


class AnswerModels(BaseModel):
    models: list[AnswerModel]
    errors: list[str] = []  # a backend that could not be listed, and why
