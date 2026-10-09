# API contract

All endpoints live under `/api`; JSON uses snake_case; errors are `{detail: string}`. The live,
authoritative schema is the OpenAPI document at `/docs` (every success body is a typed model). Local
API at 127.0.0.1:8000; the Vite dev server at 127.0.0.1:5173 proxies `/api`.

## Documents

| Endpoint | Result |
|---|---|
| `GET /documents` | `{documents: Document[]}` |
| `POST /documents` multipart `file`, optional `labels` (comma-separated) | `Document` (202). Identical content returns the existing document |
| `PUT /documents/{id}` multipart `file` | `Document` (202); the previous version stays searchable until the replacement publishes |
| `DELETE /documents/{id}` | `{ok: true}`; removes the document and its chunks |
| `GET /documents/{id}/content` | `{document, text, text_kind: "extracted"\|"original"\|"reconstructed", showing_last_published_version, provenance_note, chunks: Citation[], source_urls}` |
| `POST /documents/reindex` | `{queued: number}`: re-run indexing for documents built by an older pipeline version |
| `POST /demo` | `{documents: Document[]}` (202): loads the bundled sample corpus; `fixtures/testing/*` get label `synthetic` |

`Document`: `{id, name, content_hash, status: "queued"|"processing"|"ready"|"failed", chunk_count,
created_at, error|null, warnings[], labels[], pipeline_version, processing_stage, processing_message,
processed_chunks, total_chunks}`. Counts describe the current stage, not overall progress.

## Sources

| Endpoint | Result |
|---|---|
| `GET /sources/{chunk_id}` | `Citation` for the chunk as it is now |

`Citation` (answer citations and `GET /sources/{chunk_id}`): `{chunk_id, document_id, document_name,
text, quote|null, page|null, heading|null, source_urls[]}`.

## Questions

`POST /chat` and `POST /chat/stream` take `{question, conversation_id?, exclude_labels?: string[]}`.

`ChatResponse`: `{conversation_id, answer, status: "answered"|"insufficient_evidence"|
"conflicting_evidence", citations: Citation[], warnings[], statements: Statement[], trace:
AnswerTrace, model}`.

- `Statement`: `{text, sources: chunk_id[]}`: one sentence of the answer and the passages that state
  (part of) it, found by a separate attribution call (`KE_VERIFY_CITATIONS`). `sources: []` means no
  passage the model was shown supports the sentence. Every passage listed here is also a citation.
- `AnswerTrace`: `{search_text, rewritten, corrections: {typed: corpus word}, intent, counts: {dense,
  sparse, candidates}, reranked, bridge_terms[], passages: TracePassage[]}`: how the answer was
  produced. `passages` are exactly the passages the answer model was shown, best first.
- `TracePassage`: `{chunk_id, document_id, document, heading|null, snippet, score, ranks: {dense?,
  sparse?, hop?}, completes|null}`; `ranks.hop` marks a passage added by the second retrieval round,
  `completes: "table or list"` one that completes a neighbouring passage.

`/chat/stream` is Server-Sent Events: `status {stage, message}` reports real backend stages
(`retrieving`, `rewriting`, `embedding_query`, `reranking`, `waiting_for_model`, `generating`,
`validating`, `verifying`, `streaming`); `token {text}` replays the validated answer in segments (after generation, not raw
model tokens); `result` is the authoritative `ChatResponse`; `error {message}` ends the stream.

`POST /search` takes `{query, k=10, exclude_labels?}` and returns `{hits: [{chunk_id, document_id,
document_name, heading, text, score, ranks: {dense, sparse, hop}}], plan}`: retrieval only, no
answer generation.

`POST /preview` takes `{text, exclude_labels?}` (up to 2,000 characters) and returns `{passages:
[{chunk_id, document_id, document, heading}], corrections: {typed: corpus word}}`: at most five
passages, one per document section, that the text is likely to draw on. It calls no model (spelling
correction from the corpus vocabulary, leaving the word still being typed, then full-text search), so
the UI calls it while a question is typed.

`GET /conversations`, `GET /conversations/{id}` (`{id, messages: [{role, content, citations?,
warnings?, status?, statements?, trace?}]}`), `DELETE /conversations/{id}`. Messages saved by
versions that still had a knowledge graph load without their `graph` field.

## Health

`GET /health` returns `{status, ollama_available, models[], chat_model, embedding_model, warnings[],
index: {documents, stale_documents, chunks}}`. Warnings include missing models (chat, embedding,
and the reranker and summary models when configured), documents indexed by an older pipeline, and
chunks embedded with a different model.

## Behaviour that clients can rely on

- Source aliases (`S1`, `S2`, ...) are internal to generation; API citations carry real chunk ids.
- Every citation is a passage the answer model was shown; statement sources are always citations.
- Unknown is not false: a missing passage is never reported as a negative fact.
- An answer without a valid citation is retried once, then withheld with an explicit warning; an
  answer is never silently replaced by a fabricated one.
- Documents whose label has a configured notice (`KE_LABEL_NOTICES`) produce that notice at the
  start of any answer that cites them.
- `exclude_labels` hides those documents completely, from answers, search and the typing preview.
  Labels are matched the way they are stored (case-insensitive).
- The API answers only to the Host names in `KE_ALLOWED_HOSTS` (default `127.0.0.1`, `localhost`)
  and refuses writes whose `Origin` header is not in `KE_CORS_ORIGINS` (or this server itself), so
  a web page you visit cannot upload to or re-index your local knowledge base. Requests without an
  `Origin` header (curl, scripts) are unaffected. Foreign host: `400`; foreign origin: `403`.
- Text that PostgreSQL cannot store (NUL bytes, lone surrogates) is rejected with `422`.
