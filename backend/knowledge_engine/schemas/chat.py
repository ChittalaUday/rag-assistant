from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from knowledge_engine.schemas.graph import Citation, Highlights, KnowledgeGraph

AnswerStatus = Literal["answered", "insufficient_evidence", "conflicting_evidence"]


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    # documents carrying any of these labels are left out of retrieval (e.g. ["synthetic"])
    exclude_labels: list[str] = Field(default_factory=list, max_length=20)

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


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    status: AnswerStatus = "answered"
    citations: list[Citation]
    graph: KnowledgeGraph  # only the relations the answer rests on, all highlighted
    warnings: list[str]
    model: str
    plan: dict[str, Any] = {}  # how the question was understood and what was retrieved


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    citations: list[Citation] | None = None
    graph: Highlights | None = None  # ids only; the graph itself is GET /graph
    warnings: list[str] | None = None
    status: AnswerStatus | None = None


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: str


class ConversationList(BaseModel):
    conversations: list[ConversationSummary]


class ConversationDetail(BaseModel):
    id: str
    messages: list[Message]
