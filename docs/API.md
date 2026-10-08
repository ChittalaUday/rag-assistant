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
| `DELETE /documents/{id}` | `{ok: true}`; removes its chunks, evidence and any entity or relation nothing else supports |
| `GET /documents/{id}/content` | `{document, text, text_kind: "extracted"\|"original"\|"reconstructed", showing_last_published_version, provenance_note, chunks: Citation[], source_urls}` |
| `POST /documents/reindex` | `{queued: number}`: re-run indexing for documents built by an older pipeline version |
| `POST /demo` | `{documents: Document[]}` (202): loads the bundled sample corpus; `fixtures/testing/*` get label `synthetic` |

`Document`: `{id, name, content_hash, status: "queued"|"processing"|"ready"|"failed", chunk_count,
created_at, error|null, warnings[], labels[], pipeline_version, processing_stage, processing_message,
processed_chunks, total_chunks}`. Counts describe the current stage, not overall progress.

## Knowledge graph and entities

| Endpoint | Result |
|---|---|
| `GET /graph?min_confidence=0` | `KnowledgeGraph`: every entity and relation, nothing highlighted |
| `GET /sources/{chunk_id}` | `Citation` for the chunk as it is now |
| `GET /entities?query=&type=&limit=50&offset=0` | `{entities: [{id, name, type, degree, mentions}]}`, most connected first; matches names and aliases |
| `GET /entities/{id}` | `{id, name, type, aliases[], documents: [{id, name, mentions}], relations: Edge[]}` |

`Node`: `{id, label, type, document_ids[], mentions}`. `Edge`: `{id, source, target, label (e.g.
"reports to"), predicate (ontology id), confidence, evidence_count, evidence: EdgeEvidence[]}`.
`EdgeEvidence`: `{chunk_id, document_id, document_name, quote|null, page|null, heading|null}`, a
pointer to the passage plus the verbatim span the relation rests on, never the passage itself, so a
graph costs about 0.6 KB per edge however long the passages are. `Citation` (answer citations and
`GET /sources/{chunk_id}`) adds the passage: `{chunk_id, document_id, document_name, text,
quote|null, page|null, heading|null, source_urls[]}`; open an edge's evidence by fetching its
`chunk_id` from `/sources`. `KnowledgeGraph`: `{nodes, edges, highlighted_node_ids,
highlighted_edge_ids}`. `confidence` is extraction confidence combined over the evidence, not a
probability that the fact is true.

## Questions

`POST /chat` and `POST /chat/stream` take `{question, conversation_id?, exclude_labels?: string[]}`.

`ChatResponse`: `{conversation_id, answer, status: "answered"|"insufficient_evidence"|
"conflicting_evidence", citations: Citation[], graph: KnowledgeGraph, warnings[], model, plan}`.
`plan` is the inspectable trace: `{intent, search_text, rewritten, corrections, entities[],
retrieval: {dense, sparse, graph, candidates, selected[]}, facts, conflicts[], connections[]}`.
`graph` holds only the relations the answer rests on (backed by cited passages and mentioned by the
answer) and their endpoint nodes, all highlighted; overlay them on `GET /graph` to show them in
context. It is empty when the answer abstains.

`/chat/stream` is Server-Sent Events: `status {stage, message}` reports real backend stages
(`retrieving`, `rewriting`, `embedding_query`, `waiting_for_model`, `generating`, `validating`,
`streaming`); `token {text}` replays the validated answer in segments (after generation, not raw
model tokens); `result` is the authoritative `ChatResponse`; `error {message}` ends the stream.

`POST /search` takes `{query, k=10, exclude_labels?}` and returns `{hits: [{chunk_id, document_id,
document_name, heading, text, score, ranks: {dense, sparse, graph}}], plan}`: retrieval only, no
answer generation.

`POST /preview` takes `{text}` (up to 2,000 characters) and returns `{entities: [{id, name, type}],
highlighted_node_ids[], highlighted_edge_ids[]}`: the entities the text names (by name, alias or a
close misspelling) and the relations between them or whose predicate relates to the other words. It
calls no model (no embedding, no generation), so the UI calls it while a question is typed and
selects those nodes and edges on the map.

`GET /conversations`, `GET /conversations/{id}` (`{id, messages: [{role, content, citations?, graph?,
warnings?, status?}]}`), `DELETE /conversations/{id}`. A message's `graph` is only
`{highlighted_node_ids, highlighted_edge_ids}`: messages store ids, not graph snapshots, so storage
does not grow with the corpus and ids of relations removed since simply stop matching. Messages
saved by earlier versions with a full snapshot are returned the same way.

## Health

`GET /health` returns `{status, ollama_available, models[], chat_model, embedding_model, warnings[],
index: {documents, stale_documents, chunks, entities, relations}}`. Warnings include missing
models, documents indexed by an older pipeline, and chunks embedded with a different model.

## Behaviour that clients can rely on

- Source aliases (`S1`, `S2`, ...) are internal to generation; API citations carry real chunk ids.
- Every relation has at least one evidence row whose quote is a verbatim substring of its chunk.
- Unknown is not false: a missing relation is never reported as a negative fact.
- An answer without a valid citation is retried once, then withheld with an explicit warning; an
  answer is never silently replaced by a fabricated one.
- Documents whose label has a configured notice (`KE_LABEL_NOTICES`) produce that notice at the
  start of any answer that cites them.
- `exclude_labels` hides those documents completely: their passages, and the relations, conflicts
  and connections only they support. Labels are matched the way they are stored (case-insensitive).
- The API answers only to the Host names in `KE_ALLOWED_HOSTS` (default `127.0.0.1`, `localhost`)
  and refuses writes whose `Origin` header is not in `KE_CORS_ORIGINS` (or this server itself), so
  a web page you visit cannot upload to or re-index your local knowledge base. Requests without an
  `Origin` header (curl, scripts) are unaffected. Foreign host: `400`; foreign origin: `403`.
- Text that PostgreSQL cannot store (NUL bytes, lone surrogates) is rejected with `422`.
