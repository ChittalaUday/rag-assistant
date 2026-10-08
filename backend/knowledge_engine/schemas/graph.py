from pydantic import BaseModel


class Citation(BaseModel):
    chunk_id: str
    document_id: str
    document_name: str
    text: str
    quote: str | None = None
    page: int | None = None
    heading: str | None = None
    source_urls: list[str] = []


class Node(BaseModel):
    id: str
    label: str
    type: str
    document_ids: list[str]
    mentions: int = 0


class EdgeEvidence(BaseModel):
    """A pointer to the passage a relation rests on plus the verbatim span; the passage itself
    (and its source URLs) comes from GET /sources/{chunk_id}."""

    chunk_id: str
    document_id: str
    document_name: str
    quote: str | None = None
    page: int | None = None
    heading: str | None = None


class Edge(BaseModel):
    id: str
    source: str
    target: str
    label: str  # human-readable predicate, e.g. "reports to"
    predicate: str | None = None  # ontology id, e.g. "reports_to"
    confidence: float | None = None  # extraction confidence over all evidence, not truth
    evidence_count: int | None = None
    evidence: list[EdgeEvidence]


class Highlights(BaseModel):
    """What an answer rests on; this is all that is stored with a conversation message."""

    highlighted_node_ids: list[str]
    highlighted_edge_ids: list[str]


class EntityRef(BaseModel):
    id: str
    name: str
    type: str


class Preview(Highlights):
    """What a piece of question text mentions: the entities it names and the relations between
    them or that bear on what it asks, as ids to select on the map."""

    entities: list[EntityRef]


class KnowledgeGraph(Highlights):
    nodes: list[Node]
    edges: list[Edge]
