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
| `GET /documents/{id}/chunks` | `{document, text_chars, chunks: [{id, position, heading, text, location, summary, char_start, char_end, page, embedding_model}]}`: the passages in order, with the location line (`Title › Heading`) and model-written summary indexed with each (empty when summaries were off or failed) |
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

`POST /chat` and `POST /chat/stream` take `{question, conversation_id?, exclude_labels?: string[],
answer_language?, answer_provider?, answer_model?}`. `answer_provider` (`vllm` or `ollama`) and
`answer_model` (both or neither; 422 if the model is not listed by `GET /models/answer`) pick the model
that writes this answer; without them the default one (the model server's when `KE_CHAT_URL` is set,
else `KE_CHAT_MODEL` in Ollama) is used. Without `answer_language` the answer is written in the question's language
(retried once if a question in a non-Latin script comes back in another script). With one of the
names from `GET /languages` (`{languages: string[]}`: English, Hindi, Telugu, ...) the answer is
written in that language whatever the question's, retried once if it comes back in another script;
an unknown name is a `422`.

`ChatResponse`: `{conversation_id, answer, status: "answered"|"insufficient_evidence"|
"conflicting_evidence"|"conversation", citations: Citation[], warnings[], statements: Statement[],
trace: AnswerTrace, model}`.

`metrics` (returned and stored with every answer; absent on answers saved before it was recorded)
is for evaluations: `asked_at` (ISO, UTC; also on the user message), `route` (`small_talk` |
`documents`) and `route_score`, `first_draft_ms` (streamed requests: time to the first draft
text), `retries` (`no_valid_citations`, `wrong_language`), `statements` (`{total, supported}`),
`request` (`answer_language`, `exclude_labels`), `models` (chat, embedding, rerank), `settings`
(the answer-quality settings in force), `usage` (per call kind `generate` / `embed` / `rerank`:
`calls`, `prompt_tokens`, `output_tokens`, `model_ms`, `load_ms`, from Ollama's own counters for
this question only) and `engine` (`pipeline_version`, `code`: git commit, `+dirty` with
uncommitted changes). Stage timings stay in `trace.timings`. `make export OUT=dir` writes them all
to `exchanges.jsonl` (one row per answer with its question, status, cited documents, statement
support, timings and metrics) next to `chunks.jsonl`; it only reads the database.

`status: "conversation"` is a reply to small talk (a greeting, thanks, a question about the
assistant). Routing is semantic: the message is embedded (about 20 ms) and compared with generic,
multilingual example messages of each route (`generation/smalltalk.toml`); it is small talk when its nearest small-talk example is
closer than its nearest question example by `KE_SMALL_TALK_MARGIN` (0.11, measured for embeddinggemma:
0 of 176 fixture questions misrouted, 25 of 26 unseen small-talk messages caught; `make
eval-routing` re-measures after the examples change). Only then does
the chat model write a reply; it sees no passages and is told to state no facts, so the reply has
no citations and the trace has no passages (`intent: "conversation"`). Anything
else, and any failed or doubtful call, goes through retrieval and cite-or-abstain. An answer the
documents do not support reads "I couldn't find information about that." or the model's own short
statement of it, never a remark about "the provided sources".

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
the UI calls it while a question is typed. `graph: {entities: [{id, name, typed, found}], relations:
[{source, target, label, chunk_id, document, quote, script}]}` is read off the matched passages' text, not
extracted by a model: names are runs of capitalised words (cased scripts only), a lone name folds
into the one longer name containing it, and two neighbouring names in a sentence that shares a typed
term are related by the words between them (`appears with` when none or more than six). Each relation
quotes its sentence. `typed` marks names in the typed text; `found: false` marks a typed name that no
matched passage contains. `script` is the script most of the relation's document is written in
(`LATIN`, `TELUGU`, ...), so a client can show one language at a time.

`POST /graph` takes `{exclude_labels?}` and returns the same `{entities, relations}` for every
indexed passage (at most 400 relations, no model call): the graph shown before anything is typed.

`GET /conversations`, `GET /conversations/{id}` (`{id, messages: [{role, content, citations?,
warnings?, status?, statements?, trace?}]}`), `DELETE /conversations/{id}`. Messages saved by
versions that still had a knowledge graph load without their `graph` field.

## Health

`GET /health` returns `{status, ollama_available, models[], chat_model, embedding_model, warnings[],
index: {documents, stale_documents, chunks}}`. Warnings include missing models (chat, embedding,
and the reranker and summary models when configured), documents indexed by an older pipeline, and
chunks embedded with a different model.

## Models

`GET /models/status` returns `{ollama_available, models: [{role, model, installed, loaded, state, where,
detail, loading_seconds, size_bytes, expires_at}], error}` for the models the engine is configured to
use (`role`: `chat` answers, `embedding` search, `rerank` rescoring, `context` indexing summaries).
`state` is `loaded` (in memory, ready), `loading` (being brought into memory now; `loading_seconds` says
for how long), `asleep` (stopped to free memory, wakes on demand), `not_loaded`, `not_installed` or
`unreachable`; `where` is `local` (Ollama on this machine) or `server`.
`GET /models/answer` lists every model that can write answers: `{models: [{provider, model, state, default}],
errors}` from the model server (`vllm`; the configured model, plus the others it serves while awake) and from
Ollama (`ollama`; installed models except embedding and reranking ones). A backend that cannot be reached is
named in `errors`; the other one is still listed. `/models/status` and `/models/load` take the same
`answer_provider`/`answer_model` (query parameters, resp. body fields) to report or load that answer model.
`POST /models/load` with `{roles: [...] | null}` (null: all) brings the missing ones into memory, one at
a time, and returns the new status. A model that is not installed in Ollama is never downloaded (409
with the `ollama pull` command); Ollama or the model server being down is 503, a failed or too slow load
502 or 504 (`KE_MODEL_LOAD_TIMEOUT`, 700 s). Ollama models are kept for `KE_MODEL_KEEP_ALIVE` (30m).

**Answer model on a server.** With `KE_CHAT_URL` the answer model (`KE_CHAT_MODEL` is then the id the server
serves, e.g. `nvidia/Qwen3.6-35B-A3B-NVFP4`) runs on an OpenAI-compatible server (vLLM) instead of
Ollama: chat calls use `POST /v1/chat/completions` with the JSON schema as `response_format`;
embeddings, reranking and any other model stay on the local Ollama. Prompts, including document
passages, are sent to that server, so use only a machine you control.
With `KE_CHAT_CONTROL_URL`, `KE_CHAT_CONTROL_NAME` and `KE_CHAT_CONTROL_TOKEN` (the
[model-sleeper](../benchmark_tools/model_sleeper/README.md) beside the server) the model sleeps when idle
and the engine sees `asleep` / `loading` / `loaded` and can wake it; waking a 35B model takes 2-4
minutes, so keep `KE_MODEL_TIMEOUT` at 420 or more. The web app shows each model's state, asks before a
question has to wait for a load or wake (or does it automatically when the user chose that), and never
sends a question whose model is not installed or whose server cannot be reached.
The evaluation tools for chunking and the legacy system still call Ollama directly.

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
