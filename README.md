# Knowledge Engine

A local, domain-neutral pipeline that reads the documents you give it, builds a **hybrid search
index** over their passages, and answers questions from those passages with citations, showing for
every sentence of an answer which passage states it. Everything runs on your machine: PostgreSQL
(with pgvector) for storage and search, and a local Ollama server for the models.

```text
documents ─► parse ─► normalise ─► chunk ─► situate (optional) ─► embed ─► publish
question  ─► understand ─► retrieve (vector + full-text) ─► rerank ─► second hop ─► answer ─► attribute
```

Nothing in the engine is specific to a company or document type. The React workspace in
`frontend/` and the Niruthi/fictional documents in `fixtures/` are an example application and
sample corpus built on top of it.

- Architecture, invariants and limitations: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- HTTP API: [docs/API.md](docs/API.md) (live OpenAPI at <http://127.0.0.1:8000/docs>)
- Measured results: [docs/reports/engine-evaluation.md](docs/reports/engine-evaluation.md)

## Requirements

Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/); Node 22+ (UI only); a **local PostgreSQL
with the pgvector extension** (macOS: `brew install postgresql@18 pgvector`); and
[Ollama](https://ollama.com) with the configured models. No model is downloaded by the application:

```bash
ollama pull qwen3.5:9b                          # answers (and contextual summaries)
ollama pull embeddinggemma                      # embeddings (multilingual, 768-d)
ollama pull dengcao/Qwen3-Reranker-0.6B:F16     # reranker (optional, KE_RERANK_MODEL)
```

## Run it

```bash
make setup      # deps, backend/.env + frontend/.env, create + migrate the databases
make dev        # API on :8000, UI on :5173 (needs PostgreSQL and Ollama running)
```

Open <http://127.0.0.1:5173>, load the demo documents or upload TXT, Markdown, CSV, PDF or DOCX
files (up to 20 MB), wait for indexing, then ask questions.

### As a pipeline (no web app)

```bash
make ingest PATHS="path/to/folder other.pdf" LABELS=internal   # index files and folders
make export OUT=out                                            # the passages as JSON Lines
make reindex ARGS=--all                                        # rebuild derived data from stored files
```

`export` writes `chunks.jsonl` (text, offsets, headings). Re-indexing is idempotent, and model output
used at indexing time (contextual summaries) is cached by content, so only changed text costs model
calls.

## What you get

- **Hybrid retrieval.** pgvector (HNSW) + PostgreSQL full-text, fused by reciprocal rank, scored by
  a transparent feature model and (optionally) a local cross-encoder reranker, with typo correction
  from the corpus vocabulary, follow-up rewriting, a second retrieval round that follows the names in
  the best passage to other documents, and whole tables and lists when a passage is cut inside one.
  `POST /api/search` shows what was retrieved and why.
- **Grounded answers.** Each answer cites passages and carries a status: `answered`,
  `insufficient_evidence` or `conflicting_evidence`. Uncited answers are retried once, then withheld
  with an explicit message. A second model call maps every sentence to the passages that state it:
  it completes the citations and shows sentences that no passage supports.
- **Answer trace.** Every answer records how it was produced: the searched text and corrections,
  how many passages each search found, the passages the model was shown (grouped by document and
  section), and the passages behind each sentence. The UI shows it beside the conversation; while
  a question is typed it lists the sections the question is likely to draw on.
- **Document labels.** Label documents (for example `synthetic`) at upload; answers citing a labelled
  document carry a configurable notice, and questions can exclude labels.

## Configuration

`make setup` creates `backend/.env` and `frontend/.env` from their `.env.example` files (both are
git-ignored). Backend settings use the `KE_` prefix: database URL, Ollama URL and models, embedding
size and task prefixes, reranker model and weight, contextual summaries and their model, second-hop
retrieval, sentence verification, answer thinking, chunk and context sizes, label notices, CORS
origins and allowed hosts. The embedding model is part of the index: when you change the model or
its vector size, the next start replaces the vector column and re-embeds the documents
automatically. Schema migrations (Alembic) run at startup (`KE_AUTO_MIGRATE`) or with
`make migrate`. Both the database and Ollama are restricted to loopback addresses.

## Quality and evaluation

```bash
make check             # ruff, mypy, pytest, frontend lint/types/tests/build
make test              # backend tests with coverage (each test runs in its own PostgreSQL schema)
make eval-retrieval    # retrieval systems on the fixture corpus and question sets
make eval-answers      # 124 questions end to end through the real pipeline (ARGS="--set key=value")
make evaluate          # end-to-end questions against a running API
```

Tests use deterministic fake models, so they verify the pipeline's mechanics, not model quality.
The evaluations use the real local models. They run in a dedicated database (`knowledge_engine_test`,
or `KE_EVAL_DATABASE_URL`) and refuse any database that holds application tables.

## Example corpus and workspace

`fixtures/documents/` holds six dated notes from public sources about Niruthi Climate and
Ecosystems (provenance in `fixtures/sources.json` and [docs/reports/public-sources.md](docs/reports/public-sources.md));
`fixtures/testing/` holds five clearly fictional scenario documents, loaded with the label
`synthetic`; `fixtures/archive/northstar/` is a second fictional company used as a distractor corpus.
Public notes are a curated snapshot, not an internal knowledge base: company marketing statements are
attributed to the company, and the notes establish nothing about salaries, internal leave policy,
headcount or revenue. Uploading your own documents extends or replaces the sample corpus.

The workspace UI uses vendored [Animate UI](https://animate-ui.com/docs/components) components, shows
processing stages reported by the backend, full-document readers, the answer trace, and a toggle to
leave synthetic documents out of answers.

## Privacy

Documents, embeddings and model inference stay on this machine: loopback PostgreSQL and loopback
Ollama only, no cloud calls by the application. Development tooling (Git, Linear, Claude/Codex) sees
code and tracking data, not your documents. PDF parsing with Docling can download its own parser
weights on first use. This is an unauthenticated single-user application; network deployment and
document-level access control are out of scope.
