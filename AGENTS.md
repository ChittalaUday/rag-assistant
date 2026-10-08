# Knowledge Engine contributor rules
Build locally. Keep documents and model inference on loopback; never commit runtime data or secrets.
Use Python 3.12+, FastAPI, Pydantic, PostgreSQL + pgvector (psycopg, Alembic); React + Vite + TypeScript, Tailwind, shadcn-style Radix components, Sigma + Graphology.
The engine (`backend/knowledge_engine`) stays domain-neutral: no company, document-type or corpus-specific logic or wording in code or prompts. Domain knowledge belongs in the ontology file, document labels, settings, fixtures and the example UI.
Every graph edge must have validated verbatim evidence and a source chunk. Entity types and predicates come from the ontology; nothing is inferred or "recovered" without evidence. Never fabricate model results or silently fall back to fake answers.
Layering: api -> pipeline -> {ingestion, extraction, retrieval, reasoning, generation} -> store/llm. All SQL lives in `store/`; model output is untrusted until `extraction/validator` passes it.
Never point scratch work, evaluations or tests at the application database: tests and evaluations use `knowledge_engine_test` (one schema per run) and the evaluation harness refuses a database with application tables.
Use tests for hashing/deduplication/replacement, evidence validation, entity resolution, retrieval, follow-ups, source selection, and failures. Measure pipeline changes with `make eval-retrieval` and `make eval-extraction` before and after. Run ruff, mypy, pytest, frontend lint/typecheck/tests/build before delivery.
Agents own disjoint paths; root integrates. Do not commit or push from agents. Coordinate in Linear Company Knowledge Assistant; do not touch unrelated issues.
Prefer a single monorepo. Bound extraction, upload sizes and queues. Explain limitations in docs/ARCHITECTURE.md and docs/reports.
