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
// Where a relation comes from: a pointer to the passage and the verbatim span. The passage text
// is fetched with api.source(chunk_id), so graphs stay small.
export type EdgeEvidence = Pick<
  Citation,
  "chunk_id" | "document_id" | "document_name" | "quote" | "page" | "heading"
>;
export interface GraphNode {
  id: string;
  label: string;
  type: string;
  document_ids: string[];
  mentions?: number;
}
export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  predicate?: string | null;
  confidence?: number | null;
  evidence_count?: number | null;
  evidence: EdgeEvidence[];
}
// What an answer rests on; conversation history stores only this.
export interface Highlights {
  highlighted_node_ids: string[];
  highlighted_edge_ids: string[];
}
export interface EntityRef {
  id: string;
  name: string;
  type: string;
}
// What a piece of question text mentions (computed without a model): ids to select on the map.
export interface Preview extends Highlights {
  entities: EntityRef[];
}
export interface KnowledgeGraph extends Highlights {
  nodes: GraphNode[];
  edges: GraphEdge[];
}
export type AnswerStatus =
  "answered" | "insufficient_evidence" | "conflicting_evidence";
export interface ChatResponse {
  conversation_id: string;
  answer: string;
  status?: AnswerStatus;
  citations: Citation[];
  graph: KnowledgeGraph; // only the relations the answer rests on, all highlighted
  warnings: string[];
  model: string;
  plan?: Record<string, unknown>;
}
export interface Message {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  graph?: Highlights;
  warnings?: string[];
  status?: AnswerStatus;
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
    entities: number;
    relations: number;
  };
}
export const emptyGraph: KnowledgeGraph = {
  nodes: [],
  edges: [],
  highlighted_node_ids: [],
  highlighted_edge_ids: [],
};

export interface DocumentContent {
  document: Document;
  text: string;
  chunks: Citation[];
  text_kind: "extracted" | "original" | "reconstructed";
  showing_last_published_version?: boolean;
  provenance_note?: string | null;
  source_urls?: string[];
}
