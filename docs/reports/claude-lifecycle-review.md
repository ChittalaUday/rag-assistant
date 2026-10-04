# API lifecycle review — disposable-doc check

Scope: trace exact endpoints, dedup/replacement semantics, test coverage gaps,
provenance risk hot-spots, and a safe-to-run protocol while the six-doc live
evaluation is in flight. All line refs are
`backend/company_assistant/main.py` unless noted.

## Lifecycle call sequence

| Step | Method / path | Request | Status | Assert on response |
|---|---|---|---|---|
| Upload | `POST /api/documents` | multipart `file=@disposable.txt` | 202 | `id`, `content_hash` (sha256 of bytes), `status=="queued"` (then poll until `ready`) — L543 |
| Poll until ready | `GET /api/documents` | — | 200 | `documents[*].status` becomes `ready`; capture `chunk_count`, `warnings` — L474 |
| Duplicate upload (same bytes) | `POST /api/documents` | multipart same bytes | 202 | Returns the SAME existing document dict; **`id` equals original `id`**, no new row — L500-524 |
| Inspect chunk | `GET /api/graph` then `GET /api/sources/{chunk_id}` | — | 200 | `chunk_id`, `document_id`, `document_name`, `text`, `page`, `quote` (null here), `source_urls` list — L582, L195-209 |
| Update content | `PUT /api/documents/{id}` | multipart new bytes, different filename | 202 | 202 Accepted; `content_hash` initially unchanged, then after `ready` → new hash, new `name`, new `chunk_count` — L547 |
| Open SSE stream | `POST /api/chat/stream` | JSON `{"question": "...", "conversation_id": null}` | 200 | `Content-Type: text/event-stream`; frames `event: status`, repeated `event: token` (80-char slices), terminal `event: result` carrying `{conversation_id, answer, citations, graph, warnings, model}`; failures emit `event: error` — L805-829 |
| Conversation history | `GET /api/conversations` then `GET /api/conversations/{id}` | — | 200 | `conversations[*].id/title/created_at`; detail returns `{id, messages: [{role, content, ...}]}` with 2 msgs per turn (user+assistant) — L586-600 |
| Delete | `DELETE /api/documents/{id}` | — | 200 | `{"ok": true}`; 409 if status is `processing` — L551 |
| Verify graph purge | `GET /api/graph` | — | 200 | `nodes`/`edges` no longer reference deleted doc; `edges` where `evidence[*].document_id == deleted` are gone (clear cascades edges via chunks) — L188-193, L578 |

## Dedup/replacement semantics

- **Hash**: `hashlib.sha256(data).hexdigest()` on raw upload bytes (L499). UNION probe against `documents.content_hash` and `pending_documents.content_hash` (L500-504).
- **Duplicate POST**: no 409 — returns 202 with the existing doc dict. The signal is `response.json()["id"] == original_id` and no change in `GET /api/documents` length. If the existing row is `failed`, POST re-queues it (L510-523).
- **Replacement PUT**: ID preserved; new bytes staged in `pending_documents` with new `path` + `content_hash` (L528-532); old chunks/edges/nodes retained until the new job commits atomically (`BEGIN` … `clear(id)` … `UPDATE documents SET name=?,content_hash=?,path=?` L341, L387-392). A failed replacement keeps old evidence (name/hash unchanged — L82-96 of test). Same bytes as the current doc short-circuits as a duplicate; same bytes as a *different* doc returns 409 (L507).
- **Still-processing replacement** → 409 "Document is still processing" (L486). Delete during processing → 409 (L554).

## Existing test coverage

Covered in `backend/tests/test_api.py`:

- Full happy path incl. dup POST (L45), delete removes edges (L67).
- Failed replacement retains old evidence (L82) and retries after process restart (L297).
- Quote/label verbatim guards (L70, L222, L290); concept-endpoint recovery warning (L368).
- SSE error framing (L99, L159-163); conversation persistence across restarts (L141).
- Source citation + `source_urls` from headers (L441); upload size/type guards (L110).
- Chunk boundaries (L429); candidate-edge ranking (L414); prompt budget trimming (L231, L323, L341).

Gaps worth watching when a defect appears:

- No test asserts the HTTP status returned on duplicate POST (only `id` equality) — if someone later makes dup a 409, callers break silently.
- No test for `GET /api/sources/{missing_id}` → confirms 404 path (L201).
- No test of hash *change* across PUT (replacement tests use invalid bytes or new text but never explicitly assert the new `content_hash` differs from the old).
- No multi-document graph isolation test (deleting doc A keeping doc B's edges).
- SSE token framing shape (80-char slice) is not asserted — only `event: result` presence.

## Risk hot-spots for false-positive citations

- **Quote-in-text check**: `r.quote not in text` (L359) — plain substring on the raw chunk. Any unicode normalization or whitespace rewriting upstream will silently fail-closed, dropping real evidence (becomes a false negative, not positive). Watch for `warnings` containing "Rejected relationship" with no edges.
- **Endpoint-in-quote**: `r.source.casefold() not in r.quote.casefold()` and same for `target`/`label` (L360-362) — case-insensitive substring. False positive risk: a short entity name that is a substring of unrelated words inside the quote (e.g. entity "AI" matching "said", or "Nova" matching "innovate"). Live corpus `Niruthi` / `Climate` are long enough to be safe but short acronyms are the trap.
- **Concept-endpoint recovery**: L368-375 fabricates an `Entity(..., type="entity")` for any endpoint missing from the extraction, provided the quote passed. If the model emits a plausible-looking quote with a verbatim fake endpoint phrase, a bogus node slips in. Audit `warnings` for "Recovered source-backed relation endpoint omitted".
- **Citation ID filter in chat**: L749-755 only keeps `citation_ids` whose chunk is in the retrieved `selected` set. Model-invented IDs are dropped (warning at L752). Good. But `relation_ids` filter at L757-762 only requires the relation touch a cited chunk — a relation with weak textual relevance to the question but adjacent to a correctly cited chunk still gets highlighted.
- **Source URL extraction**: `provenance.py` L11 requires `^Source:[ \t]*…[ \t]*$` with `re.MULTILINE`. A `Source:` line with a trailing CRLF `\r` fails the `$` anchor on some inputs; verify with `GET /api/sources/{id}` that `source_urls` matches the raw headers you upload.
- **Lexical ranker tiebreak**: L632-640 `cosine + lexical`. For a disposable doc with a tiny chunk count the ranker will almost always surface your chunk; a false "citation found" against the live six-doc index is possible if the disposable text shares salient terms with Niruthi content — so phrase the probe question around a unique nonce string in the disposable text.

## Safe disposable-doc protocol (does not disturb the live index)

The live evaluation already uses the shared backend process, SQLite file,
upload dir and the single Ollama model lock (L259 `model_lock`). Anything you
upload is appended to the same index. Rules to not corrupt the run:

1. Do **not** call `/api/chat` or `/api/chat/stream` while `work/run-live.py`
   is active — it serializes on `model_lock` and will stall or interleave with
   the eval generation, polluting its answers. SSE verification must wait
   until the live evaluator writes its final JSON.
2. Do **not** `POST /api/documents` — ingestion queues through the same
   worker and competes for Ollama. Even a 20-byte doc triggers `embed` +
   `generate` under the shared lock.
3. Idempotent GETs that are safe now: `GET /api/health`, `GET /openapi.json`,
   `GET /api/documents`, `GET /api/graph`, `GET /api/sources/{chunk_id}` (pick
   a `chunk_id` from the current `/api/graph` response), `GET
   /api/conversations`. Use these to confirm wire shape without inference.
4. Full lifecycle (upload → dup → PUT → SSE → delete) should run **after**
   `work/run-live.py` exits — check `docs/reports/live-evaluation.json` has
   its final record and no Ollama request is in flight (`lsof -i :11434` or
   `curl -s http://127.0.0.1:11434/api/ps`).
5. Use a nonce-tagged doc so false positives are obvious:
   `printf 'Mira leads NonceCo-%s.\n' "$(uuidgen)" > /tmp/disposable.txt`
   then upload. Pick a nonce absent from the Niruthi corpus so citation
   validity is unambiguous.
6. Clean up: `DELETE /api/documents/{id}` and re-check `GET /api/graph` for
   zero references to that document's chunk IDs. The worker also garbage
   collects orphaned files via `clean_uploads` (L272-284) on each successful
   job and on delete (L560).

Verified live-run status indicator you can poll without disturbing anything:
`GET /api/documents` — chunk_count and status for the six active notes.
