export interface Document {
  id: string;
  name: string;
  content_hash: string;
  status: "queued" | "processing" | "ready" | "failed";
  chunk_count: number;
  created_at: string;
  error: string | null;
  warnings: string[];
  labels?: string[];
  pipeline_version?: number;
  processing_stage?: string | null;
  processing_message?: string | null;
  processed_chunks?: number;
  total_chunks?: number;
}
export interface Citation {
  chunk_id: string;
  document_id: string;
  document_name: string;
  text: string;
  quote: string | null;
  page: number | null;
  heading?: string | null;
  source_urls?: string[];
  source_url?: string;
}
// One sentence of an answer and the passages (chunk ids) that state it; [] = none does.
export interface Statement {
  text: string;
  sources: string[];
}
export interface TracePassage {
  chunk_id: string;
  document_id: string;
  document: string;
  heading: string | null;
  snippet?: string; // the start of the passage, one line
  score: number;
  ranks: Record<string, number>; // dense | sparse | hop -> 1-based rank
  completes?: string | null; // added because it completes a neighbour ("table or list")
}
// How an answer was produced: what was searched and which passages the model was shown.
export interface AnswerTrace {
  search_text: string;
  rewritten: boolean;
  corrections: Record<string, string>;
  intent: string;
  counts: { dense: number; sparse: number; candidates: number };
  reranked: boolean;
  bridge_terms: string[];
  passages: TracePassage[];
}
// Passages a question being typed is likely to draw on (keyword search, no model call).
export interface SearchPreview {
  passages: Pick<
    TracePassage,
    "chunk_id" | "document_id" | "document" | "heading"
  >[];
  corrections: Record<string, string>;
}
export type AnswerStatus =
  "answered" | "insufficient_evidence" | "conflicting_evidence";
export interface ChatResponse {
  conversation_id: string;
  answer: string;
  status?: AnswerStatus;
  citations: Citation[];
  warnings: string[];
  statements: Statement[];
  trace: AnswerTrace;
  model: string;
}
export interface Message {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  warnings?: string[];
  status?: AnswerStatus;
  statements?: Statement[];
  trace?: AnswerTrace;
}
export interface Conversation {
  id: string;
  title: string;
  created_at: string;
}
export interface Health {
  status: string;
  ollama_available: boolean;
  chat_model: string;
  embedding_model: string;
  models: string[];
  warnings: string[];
  index?: {
    documents: number;
    stale_documents: number;
    chunks: number;
  };
}
export interface DocumentContent {
  document: Document;
  text: string;
  chunks: Citation[];
  text_kind: "extracted" | "original" | "reconstructed";
  showing_last_published_version?: boolean;
  provenance_note?: string | null;
  source_urls?: string[];
}
