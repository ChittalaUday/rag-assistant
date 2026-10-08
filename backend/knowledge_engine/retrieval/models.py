from dataclasses import dataclass, field


@dataclass(frozen=True)
class LinkedEntity:
    id: str
    name: str
    type: str
    matched: str  # the (normalised) words of the question that matched
    degree: int


@dataclass
class QueryPlan:
    """What the query pipeline decided to do, kept so answers can be traced and debugged."""

    question: str
    search_text: str  # standalone question used for retrieval
    rewritten: bool
    terms: list[str]
    entities: list[LinkedEntity]  # entities the question names (facts, conflicts, highlights)
    intent: str  # lookup | relationship | overview
    final_k: int
    hops: int
    corrections: dict[str, str] = field(default_factory=dict)  # misspelling -> corpus word
    seeds: list[LinkedEntity] = field(default_factory=list)  # entities that steer retrieval


@dataclass
class Candidate:
    chunk_id: str
    ranks: dict[str, int] = field(default_factory=dict)  # retriever -> 1-based rank
    scores: dict[str, float] = field(default_factory=dict)  # retriever -> raw score
    rrf: float = 0.0
    final: float = 0.0
    features: dict[str, float] = field(default_factory=dict)
