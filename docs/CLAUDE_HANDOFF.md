# Claude continuation handoff

Updated: 2026-10-05 (Asia/Kolkata). Repository: /Users/udaychittala/exp/company-assistant.
GitHub: https://github.com/ChittalaUday/company-assistant (private, active gh account ChittalaUday).
Linear: Company Knowledge Assistant, P-UDA-2, https://linear.app/udai-kumar015/project/company-knowledge-assistant-63349891cdab.

The human explicitly authorizes local implementation, GitHub CLI actions, Claude/Codex task sharing and Linear progress updates without additional approval pauses. Follow AGENTS.md and CLAUDE.md. Browser access to http://127.0.0.1:5173 was denied by browser permission policy; do not circumvent it or execute browser commands until the user grants access. API requests and unit/build/model checks continue normally.

## Current intent

Build the local RAG company assistant with cited answers on the left and a document-derived entity/relationship map on the right. Nodes and edges must come from documents, with exact quotations for every edge. Hashing handles deduplication and updates. Use the real company Niruthi Climate and Ecosystems and verified public sources; the previous fictional Northstar corpus is archived outside active fixtures/documents. Never invent internal HR policies, ownership, contracts, prices or revenue.

## Implemented work

Backend includes FastAPI endpoints, SQLite, local Ollama, bounded processing, SHA-256 deduplication, staged replacement metadata, persistence, source evidence validation, retrieval, conversation history and selected graph highlights. Frontend includes document controls, chat/history, exact evidence drawer, Sigma/Graphology map, accessible relationships and responsive panels. Tests cover failed replacements, unrelated quotes, job recovery, question/history context, stale navigation and SSE failures. Real Docling DOCX/PDF smoke passed. Real LightRAG two-document evaluation passed queries/deletion but default quotation provenance did not fit; use application-owned validated graph.

## Active Claude work and ownership

- UDA-47: Claude CLI via evaluation worker researches primary sources, writes six short Niruthi public notes, fixtures/sources.json and 30 expected-evidence questions. Verify fetched URLs and dates, source budgets and no invented facts.
- UDA-46: Claude CLI via frontend worker updates Niruthi branding/public corpus controls and public source links. Worker independently runs frontend checks. Citation source_urls is an optional string array.
- UDA-45: Claude CLI via backend worker ranks candidate graph relationships before prompt budget, adds source_urls provenance and regression tests. Worker independently runs backend gates.
- UDA-48: Coordinator verified gh/Claude/Ollama authentication and created private repo; needs final commit/push and reproducible runbook.
- UDA-49: Coordinator integrates and executes real model corpus evaluation; independent reviewer resolves concrete correctness findings.

Do not duplicate a worker that is still running. Read docs/PROGRESS.md, docs/reports/review.md and current Linear comments first. The coordinator updates Linear for actual CLI contributions; never claim a task is done based only on a worker message.

## Remaining acceptance work

- [x] Six active Niruthi notes and 30 questions confirmed public-source summaries with Source: HTTPS headers and Snapshot: 2026-10-05 (docs/reports/claude-provenance.md).
- [x] Marketing statements attributed to the company site; logos/badges/old reports do not establish current facts. Five URLs flagged in docs/reports/claude-provenance.md for future web verification.
- [x] Local API restarted on final code; active index is only the six public notes. Development/fictional sqlite files live under ignored work/.
- [x] `make check` and `make test` green (26 Python tests, 17 frontend tests, 82% coverage; no assertion weakened).
- [x] Ingestion monitor confirmed all six documents `ready` with 121 nodes, 47 edges, zero invalid evidence (docs/reports/ingestion.json).
- [x] Real local Ollama 30-question evaluation run; latest `docs/reports/live-evaluation.json` has 18/30 smoke pass. Residual REVIEW cases categorised in docs/reports/final-verification.md.
- [x] Software defects fixed with regression tests: stopword-biased retrieval ranker, chat instruction attribution clarification, scorer regex synonym gap. Model issues (Q13/Q15 abstention, model terseness) flagged separately.
- [x] API lifecycle verified against a nonce-tagged disposable document: upload / SHA-256 dedup / replacement / source / history / SSE / delete / graph purge, Niruthi corpus intact afterwards (docs/reports/lifecycle.json). Browser verification remains unavailable (permission denied).
- [x] README, progress, final verification report updated with exact results and limitations.
- [x] Committed source, docs and public fixtures only; data/, work/, .env excluded by .gitignore. See git log and PR links in docs/reports/claude-continuation.md.
- [x] Linear UDA-45..49 updated with evidence and status per docs/reports/claude-continuation.md.

## Runtime commands

```bash
cd /Users/udaychittala/exp/company-assistant
make setup
make dev
# API: http://127.0.0.1:8000/docs; UI: http://127.0.0.1:5173
make check
make test
make evaluate
```

Use installed qwen3.5:9b and all-minilm:l6-v2; do not silently download or replace models. The extraction validator can recover an omitted literal endpoint only after quotation and label checks pass, with a visible model warning. Semantic direction and answer entailment still require review.

## Live acceptance handoff

The six public Niruthi notes,30questions andprimary-source manifest are nowverified andactive. HistoricalNorthstar corpusarchived. Backend/frontend/public-corpus Claude contributions integrated. makecheck/maketest passed23Python17frontend82%coverage. Root runtime backendPID81614(8000),frontend5173. work/run-live.py monitorsingestion thenwrites incremental docs/reports/live-evaluation.json. Do notlaunchcompetingOllama untilitends.

DetachedClaudecompletion supervisorPID82249 ownsremainingwork; actualClaudePID andstatusinignoredwork/claude-coordinator/continuation-status.json. Prompt specifies twosubagents, directLinearcomments, live30answersemanticreview, software/modelissue distinction, APIlifecycle, finalgates, commit/push/CI andoutputs/RESULTS.md. Quiet30minuteCodexappheartbeat configured. Codexcannotbeawoken directlybyClaude; heartbeatresumescoordinationwhenusageallows. Do notcreatequotaresetorcronworkarounds. Currentbrowserpermissiondenialstillapplies.

## Completion snapshot (2026-10-05, Claude continuation)

Acceptance work above is checked off. Session detail, final numbers, and model
risks are in `docs/reports/final-verification.md`; subagent audits in
`docs/reports/claude-provenance.md` and `docs/reports/claude-lifecycle-review.md`;
lifecycle evidence in `docs/reports/lifecycle.json`; latest 30-question record
in `docs/reports/live-evaluation.json`. Per-session continuation metadata and
the pointer to the user-facing summary (`~/Documents/Codex/2026-10-05/i-x20/outputs/RESULTS.md`)
is in `docs/reports/claude-continuation.md`. Linear UDA-45..49 reflect factual
status; UDA-49 kept In Review due to the standing browser permission denial.
Heartbeat should be paused by Codex when it next resumes; this Claude did not
touch cron/scheduler configuration.

## Correction to the read-only coordination review

The completed Claude coordination report reflects stale review prose for the question/history truncation finding. Actual backend code already preserves the full question and whole history records, with regressions passing; do not reopen or duplicate that fixed task based only on review.md. The Q19 `homepage` finding is also incorrect: public-source-boundaries.md includes the literal word in its body. Inspect actual current source before modifying expected assertions. Backend scoped final review confirms no further edits required.

The first read-only Claude coordinator attempted Linear with an allowed-tool namespace that did not match its actual mcp__claude_ai_Linear__save_comment name; that call was denied by its CLI dontAsk mode. Root posted actual progress with its authorized connector. Completion supervisor is separately launched with mcp__* authorization. If that fails, record the actual denial and preserve local update bodies for root; do not claim direct comments were posted.

Public ingestion finished: six ready documents,13chunks,121nodes,47edges,zero storedinvalidquotes/endpoints/labels. docs/reports/ingestion.json saved. Everydoc containsmodelwarnings forrecovered omittedendpoints and rejectedinvalidrelationships; flagmodelquality honestly. Livequestionrunisnowinprogress. Claude successfullyposted ownLineardirectcomment6c749c99-2398-41ff-9fe4-fac319c8dffd,session1243b529-a677-45e9-8625-93b9991f0ecb,no permissiondenials,usingexplicittoolmcp__claude_ai_Linear__save_comment.
