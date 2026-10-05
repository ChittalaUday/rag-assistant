# Niruthi Company Assistant

A local company knowledge assistant with answers and citations beside a document-derived relationship map. The active starter corpus is a **public-source snapshot of Niruthi Climate and Ecosystems**, prepared on 2026-10-05. It is not an internal company knowledge base. Upload your own documents to extend the workspace; nodes and relationships derive from document content rather than predefined keywords.

## Run on your Mac

Requires Python 3.12 or 3.13, uv, Node 22+, and Ollama with the configured `qwen3.5:9b` and `all-minilm:l6-v2` models. These models were available on the development machine; no model download is silently started by this application.

```bash
cd /Users/udaychittala/exp/company-assistant
make setup
# Ensure the Ollama app/server is running.
make dev
```

Open [the local workspace](http://127.0.0.1:5173). Click **Load Niruthi & fictional test documents** to ingest the six source-linked public fact notes in `fixtures/documents/` and five clearly fictional internal scenarios in `fixtures/testing/`, or upload TXT, Markdown, PDF or DOCX files up to 20 MB. Wait for processing to finish before asking questions. Initial extraction loads the local model and can take several minutes. The sidebar shows processing errors and warnings and offers replacement/retry and deletion.

Answers appear after local generation and validation; SSE then delivers answer segments to the interface. This is incremental display after generation, not token streaming directly from Ollama. Conversations persist locally. Select a citation or graph connection to inspect its current source passage and the original public website links where supplied. The map includes search, fit/zoom and a keyboard-accessible relationship list; narrow screens switch between conversation and map panels.

[Backend API documentation](http://127.0.0.1:8000/docs) is available while the app runs. Both servers bind to loopback. This is an unauthenticated single-user local application; network deployment and document-level access control are outside its scope.

## Public snapshot and attribution

The manifest in `fixtures/sources.json` and [public-source report](docs/reports/public-sources.md) record provenance and boundaries. Company marketing statements are attributed to Niruthi. Website logos do not prove customer contracts, badges do not establish current certification validity, NASA background material does not imply a NASA endorsement, and a government program description does not establish a Niruthi contract.

The public notes do not establish employee salaries, internal leave policy, current headcount or annual revenue. Supplemental leave, project, team, working-day and employee-rule scenarios are fictional testing data, not actual Niruthi policies or internal facts. Answers using them must explicitly say they are test scenarios. Questions about missing real information should abstain. The notes are a dated, curated snapshot; loading them does not fetch or continuously synchronize public websites. Existing fictional Northstar Signal documents and questions are archived in `fixtures/archive/northstar/` and excluded from active demo loading.

## How it works

FastAPI owns SQLite persistence, ingestion, model calls and retrieval. React/Vite consumes the local API; Sigma/Graphology renders the document-derived map. SHA-256 hashes deduplicate document contents. A bounded serial worker parses text/Markdown directly and PDF/DOCX through Docling, creates overlapping short chunks, embeds them locally, and extracts open entity types and relationship labels. Replacement evidence publishes atomically; failed replacement retains the active source metadata and passages. Deletion removes that document's evidence.

Retrieval combines embedding similarity, lexical overlap and graph expansion. Relationship validation requires a verbatim source quote containing both explicit endpoints and a short source-derived relational phrase. This conservative rule can omit implicit or pronominal relationships. It establishes textual provenance, not complete semantic correctness. Answers use selected passages, validated citation IDs and compact persisted conversation context. Prompts are conservatively bounded with output space reserved. The application's SQLite evidence graph is the selected engine; LightRAG remains an evaluation-only alternative.

Source navigation refetches the current chunk, so removed/replaced passages cannot silently reopen from history. Query highlights refer to source-backed selected relationships. Historical answers remain stored snapshots and still require source inspection.

SQLite, uploaded files and index data live under ignored `data/`. Application inference uses only loopback Ollama; uploaded company documents are not sent to Claude, Codex, GitHub or Linear by the application. Authenticated Claude/Codex are development tools, and Claude CLI was used for public fixture preparation and scoped code contributions. GitHub and Linear contain development code and work tracking. Docling PDF parsing can require a first-use download of parser model weights, separate from company text.

## Configuration

Copy `.env.example` to `.env` to configure Ollama, models, timeout or storage. The embedding model is part of the index: changing it requires rebuilding/re-ingesting documents in a fresh data directory. Do not reuse vectors generated by another model.

## Quality and evaluation

The current interface uses vendored official [Animate UI components](https://animate-ui.com/docs/components) for buttons, dialogs, tabs, tooltips, expandable sections and progress. Bundled Geist fonts and the application API remain local. Full-document readers show extracted/original text, chunks, source URLs and document metadata, and refresh after document processing or replacement. Ingestion captions report actual backend phases and chunk counts; conversation captions distinguish model queueing, retrieval, generation, validation and displaying the validated answer. Answer segments are streamed after validation.

The demo loader includes six public Niruthi notes and five explicitly fictional testing documents for leave, projects, teams, working days and employee rules. Ask about the fictional test scenario to exercise these policies; explicit actual Niruthi questions use public sources and retain that scope through follow-ups. See [the current iteration verification](docs/reports/repeated-response-fix.md) and [live checks](docs/reports/iteration-live-checks.json) for the citation-schema fix, scope behavior and final checks. The older evaluation below remains a historical baseline.

```bash
make check
make test
# With the app running and the active Niruthi notes indexed:
make evaluate
# Optional historical fictional LightRAG probe:
uv run --extra evaluation python scripts/evaluate_lightrag.py
```

Automated tests inject a deterministic provider to verify lifecycle, provenance, failure handling and UI behavior. They are not local-model quality results. The active evaluator runs the 30 Niruthi questions in `fixtures/questions.json`, including cross-document, follow-up, source-awareness and unknown cases. It records latency, expected-term/source coverage, graph grounding and warnings. These smoke metrics do not establish semantic answer correctness; inspect captured answers and their passages. See [final verification](docs/reports/final-verification.md) for the latest run, retrieval/scorer fixes applied this session, and flagged model limitations (persistent qwen3.5:9b abstentions on Q13/Q15 reproduced three times).

[LightRAG observations](docs/reports/lightrag.md) and `docs/reports/model-benchmark.json` preserve the earlier **fictional** two-document engine probe. Its timings and answers are not measurements over the Niruthi corpus. Read current reports for actual evaluation outcomes; do not infer that authored browser checks have run. Browser verification requires permission from the desktop approval system.

## Project and collaboration

The monorepo contains `backend/`, `frontend/`, `fixtures/` and `scripts/`. [The design](docs/superpowers/specs/2026-10-05-company-assistant-design.md), [execution plan](docs/superpowers/plans/2026-10-05-company-assistant.md), [API contract](docs/API.md), and [coordination record](docs/COORDINATION.md) define scope. `AGENTS.md` and `CLAUDE.md` govern contributors. Runtime data, secrets and temporary logs are ignored.

The user requested shared Codex/Claude tasks, disjoint file ownership, shared progress records in the existing Linear project, and verified integration by the coordinator. Contributors return actual commands, results, model issues and CLI cost metadata; they do not invent benchmarks or modify unrelated issues.

[GitHub repository](https://github.com/ChittalaUday/company-assistant) · [Linear project](https://linear.app/udai-kumar015/project/company-knowledge-assistant-63349891cdab)
