# LightRAG local evaluation — 2026-10-05

**Decision: use the application's SQLite retrieval and validated evidence graph.** LightRAG 1.5.7 answered this small connected-document probe correctly and removed document-specific graph contributions, but its default relationship schema stores generated descriptions, source chunk IDs and file names rather than validated verbatim quotes. Adapting that provenance contract would add work beyond using an application-owned graph.

## Reproduction and actual scope

Run `scripts/evaluate_lightrag.py` with `lightrag-hku==1.5.7`, `ollama==0.6.3`, and NumPy installed. Evaluation used a separate uv environment at `/tmp/company-assistant-evaluation-env`, leaving application dependencies unchanged. Ollama was exclusively accessed over `127.0.0.1:11434`, with installed `qwen3.5:9b` and `all-minilm:l6-v2`, temperature 0, thinking disabled, 8192-token context and 2048 output-token limit. Raw outputs, graph snapshots, token counts and timings are preserved in `model-benchmark.json`. This is a smoke probe, not a 30-question application score or a statistically stable throughput benchmark.

Two short connected documents established Dev Shah's ownership of Beacon, Alder Foods' Pune pilot, and HarborLink gateways from Kestrel Systems. LightRAG used serial extraction, no extra gleaning and default storage implementations. Both documents were inserted; a cross-document query, a conversation-history follow-up, and deletion of the second document were executed.

| Operation | Measured wall time | Result |
| --- | ---: | --- |
| Initial embedding, one 12-token input | 0.664 s | 384 dimensions |
| Explicit JSON relation extraction | 6.904 s | Two valid verbatim quotes |
| Insert two documents | 17.446 s | 7 nodes, 6 edges |
| Cross-document hybrid query | 6.609 s | Correct owner, customer and vendor; file references |
| Follow-up with explicit history | 3.591 s | Correct Pune depot; file references |
| Delete pilot document | 0.045 s | Success; 4 nodes, 3 edges remained |

Later embedding batches took roughly 0.013–0.652 seconds. Chat generation timings and eval/prompt token counts are recorded per call rather than summarized as hardware throughput. There was no cloud model inference in this probe.

## Observed quality and provenance

The explicit extraction request returned `Dev Shah owns Beacon.` and `Mira Chen leads Platform.` as exact quotes. Default LightRAG extraction nevertheless emitted the relation description “Northstar Signal builds industrial telemetry which is utilized by Beacon.” This sentence does not occur in the source, and its product connection is inferred. That relationship persisted after deletion because it belonged to the surviving document. Chunk-level provenance alone therefore does not guarantee the application's required edge-level quotation.

Beacon's type changed from `organization` after the first document to `event` after the second, then returned to `organization` after deletion. It should be a product or project. Open entity typing needs validation and conservative merge behavior even with a capable local model.

The cross-document answer correctly named Dev Shah, Alder Foods and Kestrel Systems. Its references were `[1] company.md` and `[2] pilot.md`, not exact source quotations. The follow-up correctly resolved “that customer” to Alder Foods only because conversation history was supplied. This demonstrates the core API's history support, not persistence of conversation state by LightRAG.

Deletion removed Pune, HarborLink and Kestrel Systems, removed pilot-only edges and removed the pilot description/source ID from shared ownership and customer edges. **Replacement/reinsertion was not exercised**; the deletion result is evidence for cleanup in this tiny case, not proof of every failure/retry/replacement path. The application must test its own document replacement and evidence invalidation.

## Upstream evidence

The official [core API guide](https://github.com/HKUDS/LightRAG/blob/main/docs/ProgramingWithCore.md) documents initialization, insertion, query and graph management. The official [extraction prompt](https://github.com/HKUDS/LightRAG/blob/main/lightrag/prompt.py) requests relationship descriptions rather than a required verbatim quote. The official [implementation](https://github.com/HKUDS/LightRAG/blob/main/lightrag/lightrag.py) provides document deletion and recovery/rebuild machinery. These upstream references explain the interface; observed claims above come from the local run preserved in JSON.

## Practical recommendation

Keep Ollama inference and embeddings local. Extract open entities and relationships with an explicit quote field, reject every quote absent from its source chunk, and retain each evidence item separately in SQLite. Use combined embedding/text retrieval plus supported graph expansion. Own document hashes, deduplication, replacement/deletion cascades and conversation history in the application. LightRAG remains a plausible alternative for larger corpus retrieval, but this app's exact edge provenance should be established before adopting it.

## Claude/Codex fixture collaboration

Authenticated local Claude Code CLI 2.1.146 ran with `-p --permission-mode bypassPermissions --output-format json`, scoped to `fixtures/**`. It created six coherent fictional Northstar Signal documents (311–361 words each) and 30 evidence-linked questions: 10 single-document, 8 cross-document, 3 dated-conflict, 4 unknown and 5 follow-up. CLI completed successfully with no permission denials: session `e22ad84d-dd08-4c67-851f-a09b31c32815`, model `claude-opus-4-7[1m]`, 271.021 seconds, 10 turns, reported cost **$1.18890825**. Usage: 15 input tokens, 66,775 cache-creation input, 493,079 cache-read input, 20,998 output. These are the CLI's reported figures, not an independently estimated bill. The fixture-authoring CLI uses hosted Claude; application inference and company data remain local.

Codex checked all 30 schemas, source file existence and follow-up parent IDs, reviewed the six documents, and revised five follow-ups to use pronouns/references so persisted conversation context is actually needed. Unknown questions have empty expected keywords and sources and must be scored for abstention, not vacuously counted as keyword successes. All fixture documents prominently identify themselves as fictional.
