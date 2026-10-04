# Company Assistant Implementation Plan
> Agentic workers execute separate backend, frontend, and evaluation workstreams. Root owns integration and verification. User explicitly authorized autonomous execution and Claude/Codex collaboration.
**Goal:** A working local company assistant with evidence-backed answers and a query-focused relationship graph.
**Architecture:** FastAPI owns local persistence, processing, retrieval and Ollama calls; React consumes the documented API. LightRAG is evaluated before engine selection.
**Tech Stack:** Python 3.12, FastAPI/Pydantic, SQLite, Ollama, Docling; React/Vite/TypeScript, Tailwind/Radix, Sigma/Graphology.
**Spec:** docs/superpowers/specs/2026-10-05-company-assistant-design.md
## Global constraints
Local data/inference; validated source evidence for every edge; dynamic entity types; no fake model fallback; agents edit disjoint paths. API names/types are pinned in docs/API.md.
## Review focus
Replacement and deletion must not leave stale graph evidence. Identical files must not invoke models twice. Invalid model quotes must never create edges. Follow-ups must resolve from persisted history. Offline/malformed models must surface recoverable errors.
## Task 1: Backend (backend/**)
- [ ] Write behavior tests for deduplication, replacement, deletion, invalid evidence, follow-ups and offline errors; observe failures.
- [ ] Implement create_app(settings, model_client) with FastAPI lifecycle, bounded queue, SQLite store and Ollama client; implement documented API.
- [ ] Implement overlapping short chunks, parsing, cosine/lexical retrieval, graph expansion, validated relationship evidence and grounded answer citations.
- [ ] Run uv run pytest, ruff check/format, mypy; report exact results.
## Task 2: Frontend (frontend/**)
- [ ] Write API/interaction tests for streaming, source inspection, document states and failures; observe failures.
- [ ] Implement all documented API contracts in src/api.ts and types.ts; implement chat/map workspace and source evidence inspection.
- [ ] Implement Sigma/Graphology graph, fit/zoom, query highlights, node selection, accessible relation list and responsive layout.
- [ ] Run npm run check, test, lint and build.
## Task 3: Fixtures and LightRAG evaluation (fixtures/**, scripts/evaluate_lightrag.py, docs/reports/lightrag.md)
- [ ] Use Claude CLI to create six connected fictional company documents and 30 evidence-linked questions.
- [ ] Run real Ollama embedding/extraction benchmarks and a two-document LightRAG probe including query and follow-up; preserve exact output and costs.
- [ ] Report engine choice against evidence provenance and update requirements; flag local model issues with examples.
## Task 4: Integration and delivery (root)
- [ ] Pin dependencies/lockfiles, document setup and agent coordination, configure CI and quality gates.
- [ ] Integrate frontend/backend, seed fixtures, run real-model questions and browser flows including upload/delete/source navigation.
- [ ] Fix discovered regressions with meaningful tests; rerun full checks, preserve evaluation report.
- [ ] Commit verified implementation, create/use private GitHub repo via gh, push, update Linear with results and limitations.
