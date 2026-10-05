# API contract
All endpoints start /api. JSON uses snake_case. Local API at 127.0.0.1:8000; Vite at 127.0.0.1:5173.
GET /health -> {status, ollama_available, chat_model, embedding_model, models: string[], warnings: string[]}
GET /documents -> {documents: Document[]}
POST /documents multipart file -> Document (202), PUT /documents/{id} multipart file -> Document (202). POST /demo -> {documents: Document[]} (202). DELETE /documents/{id} -> {ok:true}.
Document: {id,name,content_hash,status:"queued"|"processing"|"ready"|"failed",chunk_count,created_at,error:string|null,warnings:string[],processing_stage:string,processing_message:string,processed_chunks:number,total_chunks:number}. Counts describe the current processing stage, not overall percentage. Dedup returns existing Document.
GET /graph -> Graph. GET /sources/{chunk_id} -> Citation.
Node: {id,label,type,document_ids:string[]}. Edge: {id,source,target,label,evidence:Citation[]}.
Citation: {chunk_id,document_id,document_name,text,quote:string|null,page:number|null,source_urls?:string[]}.
Graph: {nodes:Node[],edges:Edge[],highlighted_node_ids:string[],highlighted_edge_ids:string[]}.
POST /chat {question,conversation_id?:string|null} -> {conversation_id,answer,citations:Citation[],graph:Graph,warnings:string[],model:string}.
POST /chat/stream same body -> SSE events: status {stage,message}, token {text}, result (same ChatResponse), error {message}; final result authoritative.
GET /conversations -> {conversations:[{id,title,created_at}]}. GET /conversations/{id} -> {id,messages:[{role:"user"|"assistant",content,citations?:Citation[],graph?:Graph,warnings?:string[]}]}. DELETE /conversations/{id} -> {ok:true}.
Errors: {detail:string}. Graph entity types are open strings, not closed categories. Evidence must be source text; unknown questions abstain.

GET /documents/{id}/content -> {document:Document,text:string,text_kind:"extracted"|"original"|"reconstructed",showing_last_published_version:boolean,provenance_note:string|null,chunks:Citation[],source_urls:string[]}. Exact extracted full text is persisted atomically on new publication. Legacy plaintext returns original text; legacy binary falls back to explicitly labeled reconstruction. Failed replacements retain the prior published text.

Source aliases are internal generation identifiers only; API citations retain real chunk IDs. Required model fields and per-request ID enums are validated. Fictional test evidence produces an explicit provenance notice. Explicit real/actual/official Niruthi source scope excludes synthetic documents and persists across follow-ups until explicitly switched. SSE status tracks real backend operations; token events display validated segments after generation, not raw model tokens.
