# Architecture

A domain-neutral **knowledge engine**: it reads whatever documents it is given, builds a
source-backed graph of entities and relations plus a hybrid search index, and answers questions
from that evidence. Nothing in the engine knows about a particular company, domain or document
type; domain knowledge lives in an ontology file and in the documents themselves.

```text
documents ─► INDEXING PIPELINE ─► PostgreSQL (chunks · entities · relations · evidence)
                                              │
question  ─► QUERY PIPELINE   ◄───────────────┘ ─► grounded answer + citations + graph
```

Documents contain information. Entities and relations represent facts. Embeddings capture
similarity. Retrieval gathers evidence. Reasoning connects it. The LLM only communicates the result.

## Repository layout

| Path | Responsibility |
|---|---|
| `backend/knowledge_engine/` | The engine (Python package) and its HTTP API |
| `backend/tests/` | Unit and integration tests (PostgreSQL schema per test, fake models) |
| `frontend/` | Example workspace UI (React + Vite) |
| `fixtures/` | Sample corpora, question sets, and hand-labelled `gold/relations.json` |
| `scripts/` | Dev/check helpers, the live end-to-end evaluator, legacy-data migration |
| `docs/` | This file, `API.md`, dated `reports/` |

## Engine modules (`backend/knowledge_engine/`)

Dependencies point downward: `api → pipeline → {ingestion, extraction, retrieval, reasoning,
generation} → store / llm`.

| Module | Stage | Responsibility |
|---|---|---|
| `ingestion/` | parse · normalise · chunk | `parser` (TXT/MD/CSV, PDF/DOCX via Docling), `chunker` (heading-aware, exact character offsets, table-header carry-over), `quality` (skip empty templates and near-duplicates), `intake` (uploads, dedup by hash, staged replacement, reader view) |
| `extraction/` | extract · resolve · validate | `ontology` (+ `ontology.toml`), `extractor` (ontology-constrained LLM call, content-addressed cache), `validator` (deterministic rules), `resolver` (canonical entity IDs across documents) |
| `store/` | index | One repository per aggregate (`documents`, `chunks`, `graph`, `conversations`, `cache`); all SQL lives here; Alembic migrations |
| `retrieval/` | understand · retrieve · rerank | `analyzer` (follow-up rewrite, entity linking, intent), `search` (dense + sparse + graph), `graph`, `fusion` (RRF, feature rerank, duplicate-free selection) |
| `reasoning/` | reason | `evidence` (citable passages, graph facts), `paths` (explicit multi-hop), `contradictions` |
| `generation/` | generate · validate | `prompt` (source-agnostic, budgeted), `answer` (cite-or-abstain, status, highlights) |
| `pipeline/` | orchestration | `indexing` (document → published evidence, worker), `querying` (question → answer), `preview` (entities and relations named by text being typed; no model), `graph_view` |
| `llm/` | models | Loopback-only Ollama client; `gateway` serialises the single local model |
| `evaluation/` | measurement | Per-stage evaluation (retrieval, extraction) against fixtures and gold labels |
| `api/`, `schemas/` | HTTP | One router per resource; typed response models; OpenAPI at `/docs` |

Entry points: `python -m knowledge_engine {ingest,reindex,export,eval}` (no web app needed) and
`uvicorn knowledge_engine.main:app`.

## Indexing pipeline

1. **Parse** the file. **Normalise**: decode entities (`&amp;`), drop `<!-- image -->` placeholders,
   collapse padded tables. The normalised text is the document's text of record; every offset and
   quote refers to it.
2. **Chunk** at headings, then sentence/line ends (about 700 characters), keeping the heading path
   and, for split tables, the header row. Each chunk is an exact slice with `char_start/char_end`.
3. **Embed** `title › heading › text` (context travels with the chunk) into a pgvector column;
   `title › heading` also feeds a weighted full-text column.
4. **Extract** per chunk with the ontology: closed enums for entity types and predicates, relations
   asked for before entities so abstract objects are not skipped. Skipped for form templates and
   near-duplicate chunks. Raw output is cached by content, so re-indexing after a validator or
   resolver change costs no model calls.
5. **Validate** (deterministic; see rules below).
6. **Resolve** names to canonical entities across documents, record aliases.
7. **Publish** chunks, mentions, relations and evidence in one transaction. A failure at any
   earlier step leaves the previously published version intact.

### Ontology

`extraction/ontology.toml` defines entity types (with an identity `class` and whether they are
`standalone`) and typed predicates (`domain`, `range`, `symmetric`, `functional`). The default is
generic (Person, Organization, Role, Location, Product, Project, Process, Policy, Event, Document,
Concept; 20 predicates). Point `KE_ONTOLOGY_PATH` at another file to describe another domain; the
schema sent to the model, the validation, identity merging and conflict detection all derive from it.

### Validation rules (precision lives here)

- Entity names must occur in the chunk; named types must look like names (capitalised), so
  "employees" cannot pose as an organization. Abstract entities survive only if a relation uses them.
- A relation must use a known predicate, reference two *listed* entities (nothing is "recovered"),
  satisfy the predicate's subject/object types (a reversed pair is flipped when that makes it valid),
  and not be a self-loop. `related_to` is only kept between two named entities.
- Evidence is a verbatim span of the chunk (tolerating markdown emphasis and whitespace) containing
  both endpoints, stored with offsets. A quote naming neither endpoint is rejected; one naming a single
  endpoint is widened within its line (bulleted lists state the subject first).
- Confidence per piece of evidence reflects exact quote, supported phrase, specific predicate and
  strict types; relation confidence combines corroborating evidence (noisy-OR). It measures
  extraction, not truth.

### Entity resolution

Names are folded (accents, case, `&`, plurals, honorifics, legal suffixes) into a key. Resolution
order: same key, recorded alias, then trigram similarity (≥ 0.85, equal numbers, ≥ 6 characters).
Within a document a short person name joins the unique longer one (`Asha` → `Asha Rao`) and a short
organization name joins a longer name it prefixes; products and projects never merge this way.
Ambiguity stays separate. IDs derive from `(class, key)`, so re-indexing recreates, never duplicates.

## Query pipeline

1. **Understand** (`analyzer`): follow-ups are rewritten into standalone questions with one small
   model call (lexical fallback if it fails); question n-grams are linked to entities by name, alias
   and fuzzy match (typos); intent is `lookup`, `relationship` or `overview` and sets retrieval depth.
2. **Retrieve**: pgvector HNSW (dense), Postgres full-text with OR semantics (sparse), and graph
   retrieval (chunks mentioning linked entities plus evidence for relations up to two hops away,
   hubs not expanded). Lists are merged by reciprocal rank fusion. A passage that ends or begins
   inside a table or list brings the neighbouring chunk(s) along (`retrieval/structure`), so a table
   cut across chunks reaches the model whole.
3. **Rerank** with a transparent feature model (fused rank, IDF-weighted term coverage, entity
   coverage, dense similarity), then take the top k in score order, dropping near-duplicates (an
   MMR diversity penalty was measured and removed, see the evaluation report).
4. **Reason**: build citable passages (S1…), the graph facts they contain, conflicts
   (functional predicates with two objects), and explicit connection paths between linked entities.
   Paths are shown, never concluded.
5. **Generate** with a source-agnostic prompt. The model sees which document each passage comes from
   but is told not to write document names (the sources are shown beside the answer), and code
   removes any "According to <file>" lead-in that still appears. It returns `answer`, `citation_ids`,
   `status`; code derives everything else.
6. **Validate**: unknown aliases are discarded; an uncited answer gets one corrective retry, then is
   withheld with an explicit message. Highlighted relations are those whose evidence is cited and
   whose endpoints the answer mentions (or one endpoint plus an entity the question named).
   `status` is `answered`, `insufficient_evidence` or `conflicting_evidence`. The response graph
   holds only those relations; a stored message keeps just their ids.

`POST /api/search` runs steps 1–3 only and `ChatResponse.plan` records what each step decided.

## Invariants and where they are enforced

| Rule | Enforced by |
|---|---|
| Source of truth; evidence preserved | `relation_evidence` rows hold verbatim quote + offsets + chunk; validator |
| Stable identity | `extraction/resolver` (`ent_<hash of class,key>`), unique `(class, key)` |
| No invented facts | Validator rejects unlisted endpoints, unsupported quotes, type violations |
| Typed relationships | `ontology` domain/range checks |
| Contradictions retained | Both relations stored; `reasoning/contradictions`; `conflicting_evidence` status |
| Uncertainty ≠ truth | `confidence` is extraction confidence only |
| Model versioning | `chunks.embedding_model`; `relation_evidence.extractor` = model + ontology hash + prompt version; `/api/health` warns on mismatch/stale |
| Reproducibility | Deterministic IDs; idempotent re-index; content-addressed extraction cache |
| Query grounding; failure handling | `generation/answer`: cite or abstain, explicit status |
| Unknown is not false | Absence of a relation is never turned into a negative statement |
| Temporal validity | **Not implemented** (see limitations) |

## Data (PostgreSQL + pgvector)

`documents` · `pending_documents` · `document_text` · `chunks` (text, offsets, heading, `embedding
vector`, generated `fts tsvector`, HNSW + GIN indexes) · `entities` / `entity_aliases` (trigram
indexes) · `entity_mentions` · `relations` · `relation_evidence` · `extraction_cache` ·
`conversations` / `messages` (answer, citations and highlight ids; never graph snapshots, so
storage does not grow with the corpus). Foreign keys cascade, so deleting a document removes its
evidence and `graph.gc()` drops entities and relations nothing supports.

## Evaluation

Each stage is measured separately (`make eval-retrieval`, `make eval-extraction`,
`scripts/evaluate.py` for end to end). See `docs/reports/engine-evaluation.md` for results and method.

## Known limitations

- No temporal validity (`valid_from/valid_to`) or literal-valued attributes (amounts, dates); such facts
  stay in chunk text and are found by search.
- No learned reranker or cross-encoder (none ships with the local model set); the feature reranker is
  a deliberate stand-in behind `retrieval/fusion.rerank`.
- Entity merges are decided at indexing time; if a later document shows two entities are the same,
  re-index to unify them.
- Extraction quality is bounded by the local model; a ~9B model is conservative on dense prose.
- One synchronous database connection (fine for a single local user).
- English full-text configuration. The embedding model sets the vector size: a change replaces the
  column at start-up and re-embeds (chunk text and the graph are kept); Matryoshka models may be
  shortened with `KE_EMBEDDING_DIMENSIONS`.
