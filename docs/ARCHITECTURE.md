# Architecture

A domain-neutral **knowledge engine**: it reads whatever documents it is given, builds a hybrid
search index over their passages, and answers questions from those passages with citations and a
trace of how each answer was produced. Nothing in the engine knows about a particular company,
domain or document type.

```text
documents ─► INDEXING PIPELINE ─► PostgreSQL (documents · chunks · vectors · full text)
                                              │
question  ─► QUERY PIPELINE   ◄───────────────┘ ─► answer + citations + statements + trace
```

Documents contain the information; retrieval gathers the passages that hold it; the model writes
the answer from those passages only; code checks every citation and maps every sentence back to
the passages that state it.

An entity/relation graph extracted by the model was part of the engine until 2026-10-09. Measured
end to end it did not improve answers (0.758 correct with and without it on 124 questions) while
extraction cost 15–20 s per passage, so it was removed (migration 0005); two-hop questions are
handled by the second retrieval round below. See the evaluation report.

## Repository layout

| Path | Responsibility |
|---|---|
| `backend/knowledge_engine/` | The engine (Python package) and its HTTP API |
| `backend/tests/` | Unit and integration tests (PostgreSQL schema per test, fake models) |
| `frontend/` | Example workspace UI (React + Vite) |
| `fixtures/` | Sample corpora and question sets |
| `scripts/` | Dev/check helpers, the live end-to-end evaluator, legacy-data migration |
| `docs/` | This file, `API.md`, dated `reports/` |

## Engine modules (`backend/knowledge_engine/`)

Dependencies point downward: `api → pipeline → {ingestion, retrieval, reasoning, generation} →
store / llm`.

| Module | Stage | Responsibility |
|---|---|---|
| `ingestion/` | parse · normalise · chunk · situate | `parser` (TXT/MD/CSV, PDF/DOCX via Docling), `chunker` (heading-aware, exact character offsets, table-header carry-over), `situate` (optional model-written summary placing each passage in its document), `intake` (uploads, dedup by hash, staged replacement, reader view) |
| `store/` | index | One repository per aggregate (`documents`, `chunks`, `conversations`, `cache`); all SQL lives here; Alembic migrations |
| `retrieval/` | understand · retrieve · rerank | `analyzer` (follow-up rewrite, spelling, intent), `search` (dense + sparse, optional cross-encoder), `fusion` (RRF, feature rerank, duplicate-free selection), `structure` (whole tables/lists), `hop` (second retrieval round) |
| `reasoning/` | evidence | `evidence` (citable passages within the context budget, in reading order) |
| `generation/` | generate · validate · attribute | `prompt` (source-agnostic, budgeted), `answer` (cite-or-abstain, status, notices), `attribution` (each sentence → the passages that state it), `smalltalk` (greetings and thanks answered without retrieval or facts) |
| `pipeline/` | orchestration | `indexing` (document → published passages, worker), `querying` (question → answer + trace), `preview` (likely sources for text being typed; no model) |
| `llm/` | models | Loopback-only Ollama client; `server` (the answer model on a vLLM server you control, behind model-sleeper: asleep / loading / ready, wake on demand; embeddings and reranking stay on Ollama) (chat with JSON schema and optional thinking, embeddings, Qwen3-Reranker relevance via token log-probabilities); `gateway` serialises access to the local model server (loading a model to warm it is not serialised, so the UI can show progress during an answer) |
| `evaluation/` | measurement | Retrieval systems and end-to-end answers on the fixtures, rescoring of saved runs |
| `api/`, `schemas/` | HTTP | One router per resource (`models`: which models are in memory, and loading them on request); typed response models; OpenAPI at `/docs` |

Entry points: `python -m knowledge_engine {ingest,reindex,export,eval}` (no web app needed) and
`uvicorn knowledge_engine.main:app`.

## Indexing pipeline

1. **Parse** the file. **Normalise**: decode entities (`&amp;`), drop `<!-- image -->` placeholders,
   collapse padded tables. The normalised text is the document's text of record; every offset and
   quote refers to it.
2. **Chunk** at headings, then sentence/line ends (about 700 characters), keeping the heading path
   and, for split tables, the header row. Each chunk is an exact slice with `char_start/char_end`.
3. **Situate** (`KE_CONTEXTUAL_CHUNKS`): the model reads the document (or a 12,000-character window
   around the passage) and writes one or two sentences naming what the passage is about and the
   subject it leaves implicit ("contextual retrieval"). The document comes first in the prompt so
   the model server reuses its prompt cache across the passages of one document; outputs are cached
   by content in `model_cache`. The summary is search text only and never part of the evidence.
4. **Embed** `title › heading › table header [› summary]` + the passage text (with the model's
   document task prefix) into a pgvector column; the same context feeds a weighted full-text column.
5. **Publish** the chunks and refresh the corpus vocabulary in one transaction. A failure at any
   earlier step leaves the previously published version intact.

## Query pipeline

1. **Understand** (`analyzer`): follow-ups are rewritten into standalone questions with one small
   model call (lexical fallback if it fails); words found nowhere in the corpus are corrected to the
   closest corpus word (one edit up to eight letters, transposed letters included); intent
   (`lookup`, `relationship`, `overview`) sets the retrieval depth.
2. **Retrieve**: pgvector HNSW (dense, with the model's query task prefix) and PostgreSQL full-text
   with OR semantics (sparse), merged by reciprocal rank fusion.
3. **Rerank**: a transparent feature model (fused rank, IDF-weighted term coverage, dense
   similarity), then optionally a cross-encoder (`KE_RERANK_MODEL`, a Qwen3-Reranker model in
   Ollama; relevance = P(yes) / (P(yes) + P(no)) of its first token) mixed in by `KE_RERANK_WEIGHT`.
   The top k are taken in score order, dropping near-duplicates.
4. **Complete**: a passage that ends or begins inside a table or list brings the neighbouring
   chunk(s) along (`retrieval/structure`).
5. **Second hop** (`KE_SECOND_HOP`): the sentence of the first-round passages that shares the most
   words with the question supplies link words — its names (capitalised words), or its rarest words
   in scripts without capitals — that also occur in other passages; a full-text search on them adds
   at most two passages from documents not yet selected. Append-only: it never displaces what the
   question itself found.
6. **Generate** with a source-agnostic prompt (passages as S1…Sn in reading order, history for
   references only). The model returns `answer`, `citation_ids`, `status`; it reports disagreeing
   passages as `conflicting_evidence`. Code removes any `According to <file>` lead-in. Optional
   thinking (`KE_ANSWER_THINKING`) lets the model reason first, with a larger output budget.
7. **Validate**: unknown aliases are discarded; an uncited answer gets one corrective retry, then is
   withheld with an explicit message.
8. **Ground** (`KE_VERIFY_CLAIMS`): before attribution each sentence is checked against the passages
   (claim-level verification). Sentences made only of words the passages use are accepted; the rest
   are judged by the answer model in batches of ten and each flagged sentence is confirmed on its
   own (`KE_VERIFY_THINKING` adds reasoning to that confirmation). Unsupported sentences are reported
   in `warnings`; the answer is rewritten once without them (short answers) and anything still
   unsupported is removed. Passages reach the model without image file names (`![](img/Dr_Rama.png)`
   read as a person's name). Dedicated entailment models were tried and left out: the
   Bespoke-MiniCheck-7B weights are non-commercial (CC BY-NC), and the MIT MiniCheck-Flan-T5-Large
   rejected true name-and-title sentences ("Shiva Ram Krishna ... CTO") and needs PyTorch.
9. **Attribute** (`KE_VERIFY_CITATIONS`): the passages a sentence is tagged with (`[S3]`) are its sources; the answer is split into sentences (Latin and Indic sentence
   ends, list lines) and one more model call lists, for each sentence, every passage that states one
   of its facts. Those passages are added to the citations, and sentences no passage supports are
   returned with an empty list so the UI can show them.

The response carries the answer, citations, `statements` and a `trace` (searched text, corrections,
counts per retriever, reranked or not, second-hop link words, and the passages the model was shown
with how each was found); the trace and statements are stored with the message.
`POST /api/search` runs steps 1–5 only.

## Invariants and where they are enforced

| Rule | Enforced by |
|---|---|
| Answers rest on passages | Citations must be aliases of passages the model was shown (`generation/answer`); uncited answers are withheld |
| Unsupported statements are visible | `generation/attribution`: per-sentence passage lists; empty = no passage states it |
| Evidence is the document text | Passages are exact slices with offsets; summaries and headings are search context only |
| Model versioning | `chunks.embedding_model`; pipeline version per document; `/api/health` warns on mismatch/stale and on missing configured models |
| Reproducibility | Idempotent re-index; content-addressed `model_cache` |
| Query grounding; failure handling | Cite or abstain, explicit status, explicit warnings |
| Unknown is not false | A missing passage is reported as missing, never turned into a negative statement |

## Data (PostgreSQL + pgvector)

`documents` · `pending_documents` · `document_text` · `chunks` (text, offsets, heading, search
context, `embedding vector`, generated `fts tsvector`, HNSW + GIN indexes) · `lexicon` (corpus
vocabulary for spelling) · `model_cache` · `conversations` / `messages` (answer, citations,
statements and trace per assistant message). Foreign keys cascade, so deleting a document removes
its chunks.

## Evaluation

`make eval-retrieval` (retrieval systems) and `make eval-answers` (124 questions end to end, in
process, with `--set` overrides) run in a scratch schema; `scripts/evaluate.py` runs questions
against a live API. See `docs/reports/engine-evaluation.md` for results and method.

## Known limitations

- English full-text configuration and English stop words; the embedding model is multilingual but
  Indian-language search, answers and evaluation are the next workstream (UDA-94).
- No temporal validity: dated statements stay in passage text and are found by search.
- The second hop and the attribution call are heuristics of a local ~9B model: attribution can miss
  a supporting passage (a supported sentence shown as unsupported) and costs one model call per
  answer.
- Contextual summaries cost one model call per passage at indexing (cached afterwards).
- One synchronous database connection and one model server (fine for a single local user);
  indexing and answering share the model.
- The embedding model sets the vector size: a change replaces the column at start-up and re-embeds;
  Matryoshka models may be shortened with `KE_EMBEDDING_DIMENSIONS`.
