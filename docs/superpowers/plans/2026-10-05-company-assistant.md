# Niruthi Company Assistant Implementation Plan
> Agentic workers execute separate backend, frontend, and evaluation workstreams. Root owns integration and verification. User explicitly authorized autonomous execution and Claude/Codex collaboration.
**Goal:** A working local Niruthi company assistant with evidence-backed answers and a query-focused relationship graph, seeded from an attributed public snapshot and extendable with local uploads.
**Architecture:** FastAPI owns local persistence, processing, retrieval and Ollama calls; React consumes the documented API. The historical fictional LightRAG probe selected the application-owned SQLite evidence graph; LightRAG remains evaluation-only.
**Tech Stack:** Python 3.12, FastAPI/Pydantic, SQLite, Ollama, Docling; React/Vite/TypeScript, Tailwind/Radix, Sigma/Graphology.
**Spec:** docs/superpowers/specs/2026-10-05-company-assistant-design.md
## Global constraints
Local uploaded data/inference; validated source evidence for every edge; dynamic entity types; no fake model fallback; attributed public facts; Codex and authenticated Claude share concrete tasks with disjoint paths. API names/types are pinned in docs/API.md.
## Review focus
Replacement and deletion must not leave stale graph evidence or misattribute retained sources; failed replacement retains active source metadata. Identical files must not invoke models twice. Invalid model quotes, endpoint-free passages and unsupported relational phrases must never create edges. Follow-ups must resolve from persisted history. Offline/malformed models and per-job timeouts must surface recoverable errors. Bound complete prompt records with output reserve. Guard asynchronous history/source choices against stale completion.
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
## Task 3: Public fixtures and historical engine evaluation (fixtures/**, assigned evaluation scripts/reports)
- [ ] Share the active public corpus preparation with authenticated Claude CLI: six concise Niruthi source-linked fact notes, fixtures/sources.json and 30 evidence-linked questions. Review attribution, word limits and missing internal facts. Preserve earlier fictional Northstar documents/questions in fixtures/archive/northstar/.
- [ ] Preserve the completed fictional two-document LightRAG probe, embedding/extraction timings, query/follow-up/deletion results and raw metadata. Label it historical engine smoke evidence, not Niruthi answer quality.
- [ ] Run active Niruthi local-model evaluation through the real API; record actual answers, citation/source/graph checks, follow-ups, unknown abstention, latency and manually reviewed limitations. Report engine choice and Claude CLI metadata without invented outcomes.
## Task 4: Integration and delivery (root)
- [ ] Pin dependencies/lockfiles, document setup and agent coordination, configure CI and quality gates.
- [ ] Integrate frontend/backend, load active public fixtures, run real-model questions and permitted browser flows including upload/delete/source navigation. Browser approval denial must not be bypassed; report pending checks honestly.
- [ ] Fix discovered regressions with meaningful tests; rerun full checks, preserve evaluation report.
- [ ] Commit verified implementation, create/use private GitHub repo via gh, push, update Linear with results and limitations.

## Coordination and current scope
The user explicitly requested tasks shared with Claude and Codex. Claude's authenticated CLI contributes assigned backend, public-fixture and frontend tasks; Codex independently reviews and verifies. Root owns integration, GitHub/Linear updates and progress/handoff reports. Current documentation ownership is README.md, docs/COORDINATION.md and these design/plan files. Implementation checklist items are verification requirements, not assertions that every live or browser check is complete. Consult coordinator reports for actual results.

The UI says Load Niruthi public documents and Public-source snapshot, not internal policies. Original public website URLs accompany citations when known. Missing salaries, leave policy, current headcount and revenue must abstain. Application requests remain local; the hosted development CLI must not receive uploaded private company data.
