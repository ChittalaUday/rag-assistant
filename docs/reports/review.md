# Independent implementation review

Reviewed 2026-10-05 against AGENTS.md, docs/API.md, the design and implementation plan, backend main.py/tests, frontend App/GraphMap/API/tests, and evaluator. This is a snapshot while integration and frontend checks continue. No production edits, broad-suite reruns, or live Ollama calls were made. Targeted reproduction used an injected local fake model and a temporary database.

## Actionable findings

### P1 — Failed replacements misattribute retained evidence to the replacement

`backend/company_assistant/main.py`, `upload()` replacement update and `Store.citation()`.

Uploading a ready `original.txt`, then PUT-ing `replacement.txt` containing invalid UTF-8 changes the document name/hash/path before processing succeeds. Processing correctly retains original chunks/edges on failure, but citations join the newly changed document row: old source text is now attributed to `replacement.txt`. The old hash is no longer deduplicated, and the old file becomes eligible for later orphan cleanup even though its evidence remains active. Reproduced: failed status, source text `Mira leads Acme. Revenue increased.`, citation filename `replacement.txt`.

Keep active document metadata and bytes unchanged until the same transaction that publishes replacement chunks. Store pending replacement metadata separately; expose pending/failure status without representing those bytes as the active evidence. Extend replacement tests to verify citation filename, hash dedup, retries, restart, and active-file retention.

### P1 — A verbatim quote can validate a relationship it does not support

`backend/company_assistant/main.py`, `process()` valid_relations checks.

Endpoint names need only occur somewhere in the chunk; the quote need only be a substring. With source `Mira leads Acme. Revenue increased.`, extraction `{source:Mira,target:Acme,label:owns,quote:"Revenue increased."}` publishes an `owns` edge with that unrelated evidence. This was reproduced with an injected model. Open-ended extraction and verbatim substring validation are implemented, but the current validator guarantees textual provenance rather than relationship support.

Reject evidence lacking the relevant endpoint mentions unless a separately checked coreference span supports them. Verify that the quoted passage entails the extracted relation, using an explicit bounded verification step and conservative rejection/warnings; do not introduce a fixed relation keyword vocabulary. Add unrelated-quote and wrong-label cases, alongside the existing absent-quote test. The same limitation exists for answers: any valid selected citation ID admits the entire answer, even if its claim is unsupported. Real-model evaluation should manually assess claim/source and question alignment, since keyword coverage cannot verify this.

### P2 — A total job timeout kills the only worker and strands queued documents

`backend/company_assistant/main.py`, `worker()` and model-lock waits.

The outer `asyncio.wait_for(process(...))` can raise TimeoutError, but worker has only a finally clause. This ends the sole worker, leaves the interrupted document processing, and leaves all subsequent uploads queued until restart. Per-model timeouts begin after acquiring model_lock; repeated concurrent chat calls can extend job time beyond the outer limit even when each call respects its own timeout. Cancellation also bypasses process's Exception handler.

Catch the per-job outer timeout in worker, roll back incomplete state, mark that document failed with a retryable error, and continue consuming jobs. Preserve shutdown cancellation separately. Add a short-timeout regression proving that a second queued job still completes after the first times out.

### P2 — Prompt bounds do not enforce the configured token context

`backend/company_assistant/main.py`, `chat()` history/context/relation_context assembly and `Ollama.generate()`.

The prompt can contain 6,000 characters of serialized history, 14,000 of serialized candidate edges, roughly 5,500 of chunks, a 2,000-character question, plus instructions/schema. Character truncation is not a token budget for the configured 8,192-token context, especially with punctuation, code or non-English documents. History serializes whole prior graphs/citations rather than just conversational content, and candidate edge JSON duplicates source text and can include evidence from chunks outside SOURCE. Slicing JSON at 14,000/6,000 can also cut IDs or quoted data in half. An overloaded context can silently lose question/source details, defeating citation alignment and conflict resolution.

Construct a compact history of role/content, compact selected relationship records, and only selected evidence. Bound records before serialization. Apply a conservative model token budget with an explicit output reserve, and trim whole source/history records in priority order. Add dense-graph and non-English/code context tests, then verify actual context handling with the configured model.

### P2 — Asynchronous navigation/source loads can overwrite newer user choices

`frontend/src/App.tsx`, `openConversation()` and `selectEvidence()`.

Neither operation has an abort controller or a request generation guard. Click history A then B while A's API request is slower: A can overwrite B's conversation ID/messages/map after B opens. Start a history load and click New conversation: the old response reopens history. Click source A then B: late A can replace B's drawer; closing the drawer while a request is pending can reopen it. Chat busy does not protect these pending navigation loads.

Guard each view load with an incrementing request ID or abort controller and invalidate it on new chat/drawer close. Apply a history response atomically after its live graph response is ready. Add deferred-response component tests for reversed completion order and close/new-chat invalidation.

## Verified strengths and remaining validation

Entity types and relationship labels are dynamic model strings; there is no predefined graph keyword list. Extraction endpoints and verbatim substrings are checked; graph highlights are restricted to model-selected relations with cited source chunks. Successful replacement clears only that document's graph rows; dedup avoids repeat inference for unchanged ready files. Model failures become explicit errors, and missing evidence yields an abstention. Conversations persist across restart and source inspection refetches live chunks, so removed citations do not simply reopen their historic payloads.

Current tests demonstrate follow-up history presence, rather than semantic reference resolution, conflict handling, or source relevance. Root's real-model evaluation must cover those claims. Historical graph/citation payloads remain snapshots after deletion, which is acceptable for history provided the UI clearly treats missing live evidence as unavailable. Polling now preserves equal graph object identity and prunes highlights against live nodes/edges, reducing repeated Sigma camera resets; graph changes/highlight changes still reconstruct the renderer. SSE delivers tokens after complete generation, so it provides incremental display rather than lower model first-token latency.

## Scoped re-review after fixes

Reviewed the revised paths and regression tests on 2026-10-05. This section supersedes the initial findings' implementation status. No broad tests, browser actions, production edits, or live model requests were performed.

1. **Replacement metadata: resolved.** `pending_documents` separates staged name/hash/path from active metadata. Publication updates chunks and active metadata in the same transaction; failed processing retains active citations, hash and file. Cleanup includes pending paths. Dedup finds active and pending hashes, retry of staged bytes survives restart, and uploading active bytes after a failed replacement cancels pending state. The new restart/retry test checks active filename/hash/file retention and cleanup after success.
2. **Unrelated quote/wrong label: concrete reproductions resolved; semantic model risk remains.** Validation now requires both endpoint names and the extracted label in the exact quote, rejecting the original unrelated quote and absent-label triggers. Corresponding tests cover both. These checks do not prove direction or entailment: swapped endpoints, negation, or a relational word in a different clause can still pass. Treat this and answer claim entailment as explicit model limitations requiring semantic review of real output; do not describe the checks as a semantic proof.
3. **Worker timeout: resolved.** Configurable total job timeout is caught outside process; interrupted state is rolled back and marked failed while the consumer continues. Shutdown cancellation remains separate. The new stalling-model test verifies the next queued document reaches ready after the first job times out.
4. **Context overflow/partial serialized JSON: original issue resolved, but a material regression remains below.** Whole source records and compact relation records are byte-bounded before serialization; history contains only role/content; relation evidence is limited to selected source chunks. Ollama rejects prompt bytes plus schema bytes plus 2,048 output reserve above its configured 12,288 context and sets num_predict=2048. This is a conservative upper bound, though the transport/chat-template overhead has no explicit separate reserve.
5. **Navigation/source races: resolved.** Separate monotonically increasing history/source request IDs discard reversed completions and stale errors. New conversation, changing history, drawer close and unmount invalidate appropriate requests; sending a question invalidates pending history. History messages/ID/live graph are applied together after both responses. Deferred-response tests cover reverse completion, new conversation, source selection and close invalidation.

### P2 — Context fix silently drops parts of the current question and follow-up referents

`backend/company_assistant/main.py`, `chat()` now sends only the first 800 UTF-8 bytes of `q.question`, despite accepting up to 2,000 characters, and takes only the first 100 UTF-8 bytes of every history message. These are content slices, not whole conversational records. For a question beginning with 800 bytes of background followed by `Who owns Nova?`, QUESTION excludes the actual question; retrieval still considers the full request. In a previous message whose first 100 bytes are background and whose named referent occurs later, `What does she lead?` loses that referent entirely. No warning tells the user that the question or history meaning was changed.

Preserve the complete current question and allocate remaining prompt budget dynamically by dropping whole low-priority history/relation/source records. If an accepted question cannot fit with meaningful evidence, return an explicit length/budget error rather than silently answering a prefix. Preserve complete recent role/content history records where possible, or resolve references through a separate bounded step with explicit omission handling. Add regressions with the actual question after byte 800 and the follow-up referent after byte 100. Current compact-JSON tests use short questions and do not exercise these triggers.
