# Knowledge Engine contributor rules
Build locally. Keep documents and model inference on loopback; never commit runtime data or secrets.
Use Python 3.12+, FastAPI, Pydantic, PostgreSQL + pgvector (psycopg, Alembic); React + Vite + TypeScript, Tailwind, shadcn-style Radix components.
The engine (`backend/knowledge_engine`) stays domain-neutral: no company, document-type or corpus-specific logic or wording in code or prompts. Domain knowledge belongs in document labels, settings, fixtures and the example UI.
Every published answer cites passages the model was actually shown; statements are mapped to the passages that state them and unsupported ones are shown as such. Nothing is inferred or "recovered" without evidence. Never fabricate model results or silently fall back to fake answers.
Layering: api -> pipeline -> {ingestion, retrieval, reasoning, generation} -> store/llm. All SQL lives in `store/`; model output is untrusted data, validated against a JSON schema and the given passages before use.
Never point scratch work, evaluations or tests at the application database: tests and evaluations use `knowledge_engine_test` (one schema per run) and the evaluation harness refuses a database with application tables.
Use tests for hashing/deduplication/replacement, citation validation, retrieval, follow-ups, source selection, and failures. Measure pipeline changes with `make eval-retrieval` and `make eval-answers` before and after, and keep a model or feature only if it measurably helps. Run ruff, mypy, pytest, frontend lint/typecheck/tests/build before delivery.
Agents own disjoint paths; root integrates. Do not commit or push from agents. Coordinate in Linear Company Knowledge Assistant; do not touch unrelated issues.
Prefer a single monorepo. Bound model calls, upload sizes and queues. Explain limitations in docs/ARCHITECTURE.md and docs/reports.
