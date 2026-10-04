# API contract
All endpoints start /api. JSON uses snake_case. Local API at 127.0.0.1:8000; Vite at 127.0.0.1:5173.
GET /health -> {status, ollama_available, chat_model, embedding_model, models: string[], warnings: string[]}
GET /documents -> {documents: Document[]}
POST /documents multipart file -> Document (202), PUT /documents/{id} multipart file -> Document (202). POST /demo -> {documents: Document[]} (202). DELETE /documents/{id} -> {ok:true}.
Document: {id,name,content_hash,status:"queued"|"processing"|"ready"|"failed",chunk_count,created_at,error:string|null,warnings:string[]}. Dedup returns existing Document.
GET /graph -> Graph. GET /sources/{chunk_id} -> Citation.
Node: {id,label,type,document_ids:string[]}. Edge: {id,source,target,label,evidence:Citation[]}.
Citation: {chunk_id,document_id,document_name,text,quote:string|null,page:number|null}.
Graph: {nodes:Node[],edges:Edge[],highlighted_node_ids:string[],highlighted_edge_ids:string[]}.
POST /chat {question,conversation_id?:string|null} -> {conversation_id,answer,citations:Citation[],graph:Graph,warnings:string[],model:string}.
POST /chat/stream same body -> SSE events: status {message}, token {text}, result (same ChatResponse), error {message}; final result authoritative.
GET /conversations -> {conversations:[{id,title,created_at}]}. GET /conversations/{id} -> {id,messages:[{role:"user"|"assistant",content,citations?:Citation[],graph?:Graph,warnings?:string[]}]}. DELETE /conversations/{id} -> {ok:true}.
Errors: {detail:string}. Graph entity types are open strings, not closed categories. Evidence must be source text; unknown questions abstain.
