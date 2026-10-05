export interface Document {
  id: string;
  name: string;
  content_hash: string;
  status: "queued" | "processing" | "ready" | "failed";
  chunk_count: number;
  created_at: string;
  error: string | null;
  warnings: string[];
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
  source_urls?: string[];
  source_url?: string;
}
export interface GraphNode {
  id: string;
  label: string;
  type: string;
  document_ids: string[];
}
export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  evidence: Citation[];
}
export interface KnowledgeGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  highlighted_node_ids: string[];
  highlighted_edge_ids: string[];
}
export interface ChatResponse {
  conversation_id: string;
  answer: string;
  citations: Citation[];
  graph: KnowledgeGraph;
  warnings: string[];
  model: string;
}
export interface Message {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  graph?: KnowledgeGraph;
  warnings?: string[];
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
