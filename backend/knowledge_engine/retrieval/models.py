from dataclasses import dataclass, field


@dataclass
class QueryPlan:
    """What the query pipeline decided to do, kept so answers can be traced and debugged."""

    question: str
    search_text: str  # standalone question used for retrieval
    rewritten: bool
    terms: list[str]
    intent: str  # lookup | relationship | overview
    final_k: int
    corrections: dict[str, str] = field(default_factory=dict)  # misspelling -> corpus word
    answer_language: str = ""  # a key of ANSWER_LANGUAGES; empty: the question's language


@dataclass
class Candidate:
    chunk_id: str
    ranks: dict[str, int] = field(default_factory=dict)  # retriever -> 1-based rank
    scores: dict[str, float] = field(default_factory=dict)  # retriever -> raw score
    rrf: float = 0.0
    final: float = 0.0
    features: dict[str, float] = field(default_factory=dict)
