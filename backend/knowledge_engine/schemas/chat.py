from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from knowledge_engine.schemas.sources import Citation
from knowledge_engine.text import ANSWER_LANGUAGES

# conversation: a reply to small talk (a greeting, thanks), made without passages: no facts, no
# citations
AnswerStatus = Literal["answered", "insufficient_evidence", "conflicting_evidence", "conversation"]


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    # documents carrying any of these labels are left out of retrieval (e.g. ["synthetic"])
    exclude_labels: list[str] = Field(default_factory=list, max_length=20)
    # answer in this language (a name from GET /languages); None: in the question's language
    answer_language: str | None = None
    # write the answer with this model (see GET /models/answer); both or neither; None: the default
    answer_provider: Literal["vllm", "ollama"] | None = None
    answer_model: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def provider_and_model_together(self) -> "Question":
        if (self.answer_provider is None) != (self.answer_model is None):
            raise ValueError("answer_provider and answer_model go together")
        return self

    @field_validator("answer_language")
    @classmethod
    def known_language(cls, value: str | None) -> str | None:
        if value is not None and value not in ANSWER_LANGUAGES:
            raise ValueError(f"must be one of {', '.join(ANSWER_LANGUAGES)}")
        return value

    @field_validator("question")
    @classmethod
    def storable(cls, value: str) -> str:
        """The question is saved with the conversation; PostgreSQL text cannot hold NUL."""
        if "\x00" in value:
            raise ValueError("must not contain NUL characters")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("must be valid Unicode text") from None
        return value


class Statement(BaseModel):
    text: str  # one sentence of the answer
    sources: list[str]  # chunk ids of the passages that state it; empty: none does


class TracePassage(BaseModel):
    chunk_id: str
    document_id: str
    document: str
    heading: str | None = None
    snippet: str = ""  # the start of the passage, one line
    score: float
    ranks: dict[str, int]  # how it was found: dense / sparse / hop, 1 = best
    completes: str | None = None  # added because it completes a neighbour ("table or list")


class SearchCounts(BaseModel):
    dense: int = 0  # passages found by meaning
    sparse: int = 0  # passages found by keywords
    candidates: int = 0  # distinct passages ranked


class AnswerTrace(BaseModel):
    """How an answer was produced: what was searched and which passages the model was given."""

    search_text: str
    rewritten: bool = False
    corrections: dict[str, str] = {}
    intent: str = "lookup"
    counts: SearchCounts = SearchCounts()
    reranked: bool = False
    bridge_terms: list[str] = []
    passages: list[TracePassage] = []
    # milliseconds per stage: understand, retrieve, answer, check, total (absent while streaming)
    timings: dict[str, int] = {}


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    status: AnswerStatus = "answered"
    citations: list[Citation]
    warnings: list[str]
    statements: list[Statement] = []
    trace: AnswerTrace
    metrics: dict[str, Any] | None = None  # as stored with the answer (Message.metrics)
    model: str


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    asked_at: str | None = None  # user messages: when the question was asked (ISO, UTC)
    citations: list[Citation] | None = None
    warnings: list[str] | None = None
    status: AnswerStatus | None = None
    statements: list[Statement] | None = None
    trace: AnswerTrace | None = None
    # for evaluations: route, retries, models, settings, Ollama usage, code version (answers);
    # absent on messages saved before it was recorded
    metrics: dict[str, Any] | None = None


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: str


class ConversationList(BaseModel):
    conversations: list[ConversationSummary]


class ConversationDetail(BaseModel):
    id: str
    messages: list[Message]
