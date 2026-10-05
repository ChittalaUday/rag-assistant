# Bounded iteration review

2026-10-05. Codex independently reviewed stable backend/frontend changes for source aliases, public versus synthetic source scope, full-document reading, real processing progress, and SSE cancellation. No source edits, broad test runs, live Ollama calls, or browser calls were made. Authenticated Claude CLI was invoked with the user's configured default model, only Read/Grep/Glob tools, no MCP tools, and an explicit maximum of 12 tool calls in its review prompt. Its outcome is recorded below when available.

## Actionable findings and resolution

- **P1, scope filtering (resolved during review):** An explicit question `What is actual Niruthi leave policy, not the fictional scenario?` originally bypassed filtering because merely mentioning `fictional` disabled real scope. A subsequent `Who approves it?` also lost scope because only the current question was checked. Backend now gives explicit real/actual/official Niruthi requests precedence, persists conversation_scope across turns/restarts, excludes fiction-marked documents before retrieval in real scope, and supports an explicit fictional switch. New tests cover negation, immediate and later follow-ups, and scope switching. These concrete triggers are resolved.
- **P2, reader stale state (sent to frontend):** DocumentReader originally fetched only when id changed. Opening a processing document left its text/stage/count snapshot unchanged after completion, and a same-id replacement left old text indefinitely. The fix should refresh on live status/hash/progress changes or poll pending content while preserving request invalidation. Resolution is recorded after the owner's final changes.
- **P2, contract documentation (sent to root):** At the first stable review snapshot, docs/API.md omitted GET /documents/{id}/content, Document processing_stage/processing_message/processed_chunks/total_chunks, and SSE status.stage. Implementation and frontend optional field types agree; update the pinned API contract before delivery.

## Checked behavior

Required citation_ids/relation_ids are enforced by the Answer schema and an explicit omitted-field check. Model-facing citation aliases resolve back to original source IDs, and the schema restricts aliases/relationship IDs to selected evidence. Full current questions and whole conversational records are retained while auxiliary records are removed to meet a byte-based model budget.

New full document text is inserted in the same publication transaction as chunks and metadata. Failed replacement retains the old full text and active name/hash; the content response reports showing_last_published_version/provenance_note, and the reader displays that note. Legacy plain-text/Markdown/CSV falls back to original stored bytes; legacy binary content is explicitly labeled reconstructed rather than passed off as original full text.

Document phase/count updates correspond to completed embedding batches and completed extraction results, then publication/ready. Frontend captions identify the actual stage and do not invent a global completion percentage. Chat model-lock callbacks send waiting/embedding/generating phases when those operations actually begin; validating precedes answer token display.

SSE owns its producer task and cancels/awaits it in the async generator's finally block. Backend added aclose regression checking cancellation of pending model generation and no conversation publication. Reader effect cleanup suppresses outdated responses on ID changes and drawer close; history/source request generations remain guarded.

Semantic relationship direction and factual answer entailment remain model limitations rather than guarantees of substring validation. Real-corpus behavior and end-to-end integration are owned by root.

## Claude review outcome and Codex triage

Authenticated Claude CLI completed successfully (exit 0) using the user's configured default model; no model override, browser, Ollama, source mutation, or delegation was used. The bounded prompt limited the review to 12 Read/Grep/Glob tool calls and the feature paths above. Review wall time was approximately six minutes. Claude reported no defects in SSE producer cancellation, normal atomic full-text retention, reader request invalidation, progress phases/counts, or required alias schema enforcement.

Claude proposed three low-priority findings. Codex checked them against actual constraints:

- **Not actionable:** Empty citation aliases allegedly result when one chunk exceeds 4,000 UTF-8 bytes. Chunk size is at most 650 Python characters; even four-byte UTF-8 code points give at most 2,600 bytes plus a short header, so the proposed trigger cannot exceed the 4,000-byte per-request source limit.
- **Not actionable:** A legacy failed document with no chunks does not report last-published-version when a staged replacement fails. With neither stored text nor retained chunks, there is no previously published content to represent; the explicit unavailable-text note is appropriate. The common retained-evidence case sets the note correctly.
- **P3, conservative false negative:** `chat()` identifies a fictional document when any chunk contains `fictional test scenario`. A genuinely factual document discussing synthetic test conventions with that literal phrase is excluded wholesale in real scope. Prefer explicit document provenance metadata or a well-defined leading marker; retain conservative filtering until provenance is trustworthy. This concerns omission of real evidence, not fabrication of actual Niruthi facts.

Root updated docs/API.md to include the full-document endpoint, progress fields, SSE stages and source-scope contract; that documentation finding is resolved. Root reports live API checks passed for exact full-reader text, processing counts, real-source follow-ups, explicit switch to fiction, and real SSE stage order. Those live checks were not repeated by this reviewer.
