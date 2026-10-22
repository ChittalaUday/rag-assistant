import type {
  AnswerTrace,
  ChatResponse,
  Citation,
  Conversation,
  Document,
  DocumentChunks,
  DocumentContent,
  Health,
  Message,
  ModelRole,
  AnswerChoice,
  AnswerModels,
  ModelsStatus,
  PreviewGraph,
  SearchPreview,
} from "./types";
const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, options);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: string };
      message = body.detail || message;
    } catch {
      /* Keep HTTP status when response is not JSON. */
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}
function upload(file: File) {
  const body = new FormData();
  body.append("file", file);
  return body;
}
export const api = {
  health: () => request<Health>("/health"),
  // choice: the answer model picked in the header (null: the default one)
  modelsStatus: (choice: AnswerChoice | null = null) =>
    request<ModelsStatus>(
      `/models/status${choice ? `?${new URLSearchParams({ answer_provider: choice.provider, answer_model: choice.model })}` : ""}`,
    ),
  // every model that can write answers, from the model server and from Ollama
  answerModels: () => request<AnswerModels>("/models/answer"),
  // bring the models into memory (all configured ones, or only the given roles)
  loadModels: (roles?: ModelRole[], choice: AnswerChoice | null = null) =>
    request<ModelsStatus>("/models/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        roles: roles ?? null,
        ...(choice
          ? { answer_provider: choice.provider, answer_model: choice.model }
          : {}),
      }),
    }),
  documents: () => request<{ documents: Document[] }>("/documents"),
  documentChunks: (id: string) =>
    request<DocumentChunks>(`/documents/${encodeURIComponent(id)}/chunks`),
  documentContent: (id: string) =>
    request<DocumentContent>(`/documents/${encodeURIComponent(id)}/content`),
  preview: (text: string, signal?: AbortSignal, excludeLabels: string[] = []) =>
    request<SearchPreview>("/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, exclude_labels: excludeLabels }),
      signal,
    }),
  languages: () => request<{ languages: string[] }>("/languages"),
  graph: (excludeLabels: string[] = []) =>
    request<PreviewGraph>("/graph", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ exclude_labels: excludeLabels }),
    }),
  source: (id: string) =>
    request<Citation>(`/sources/${encodeURIComponent(id)}`),
  conversations: () =>
    request<{ conversations: Conversation[] }>("/conversations"),
  conversation: (id: string) =>
    request<{ id: string; messages: Message[] }>(
      `/conversations/${encodeURIComponent(id)}`,
    ),
  deleteConversation: (id: string) =>
    request<{ ok: boolean }>(`/conversations/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  upload: (file: File) =>
    request<Document>("/documents", { method: "POST", body: upload(file) }),
  replace: (id: string, file: File) =>
    request<Document>(`/documents/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: upload(file),
    }),
  deleteDocument: (id: string) =>
    request<{ ok: boolean }>(`/documents/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  demo: () => request<{ documents: Document[] }>("/demo", { method: "POST" }),
  documentRawUrl: (id: string) =>
    `${API_BASE}/documents/${encodeURIComponent(id)}/raw`,
};
export async function streamChat(
  question: string,
  conversation_id: string | null,
  onToken: (text: string) => void,
  onStatus: (message: string, stage?: string) => void, // stage: the pipeline step starting
  signal?: AbortSignal,
  excludeLabels: string[] = [],
  onTrace?: (trace: AnswerTrace) => void,
  onDraft?: (text: string) => void,
  answerLanguage: string | null = null, // null: answer in the question's language
  answerChoice: AnswerChoice | null = null, // null: the default answer model
): Promise<ChatResponse> {
  const response = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      conversation_id,
      exclude_labels: excludeLabels,
      ...(answerLanguage ? { answer_language: answerLanguage } : {}),
      ...(answerChoice
        ? {
            answer_provider: answerChoice.provider,
            answer_model: answerChoice.model,
          }
        : {}),
    }),
    signal,
  });
  if (!response.ok) {
    const body = (await response.json()) as { detail?: string };
    throw new Error(body.detail || `Chat failed (${response.status})`);
  }
  if (!response.body) throw new Error("Streaming response unavailable");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: ChatResponse | undefined;
  function consume(frame: string) {
    let event = "message";
    const data: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (!data.length) return;
    let payload: unknown;
    try {
      payload = JSON.parse(data.join("\n"));
    } catch {
      throw new Error("Received malformed streaming data");
    }
    const value = payload as Record<string, unknown>;
    if (event === "error")
      throw new Error(String(value.message || "Model request failed"));
    if (event === "token" && typeof value.text === "string")
      onToken(value.text);
    if (event === "draft" && typeof value.text === "string")
      onDraft?.(value.text);
    if (event === "status" && typeof value.message === "string")
      onStatus(
        value.message,
        typeof value.stage === "string" ? value.stage : undefined,
      );
    if (event === "trace" && typeof value.search_text === "string")
      onTrace?.(payload as AnswerTrace);
    if (event === "result") {
      if (
        typeof value.answer !== "string" ||
        typeof value.conversation_id !== "string" ||
        !Array.isArray(value.citations) ||
        !Array.isArray(value.statements) ||
        typeof value.trace !== "object" ||
        value.trace === null
      )
        throw new Error("Received malformed final answer");
      result = payload as ChatResponse;
    }
  }
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer = (buffer + decoder.decode(value, { stream: !done })).replace(
        /\r\n/g,
        "\n",
      );
      let index;
      while ((index = buffer.indexOf("\n\n")) !== -1) {
        consume(buffer.slice(0, index));
        buffer = buffer.slice(index + 2);
      }
      if (done) break;
    }
    if (buffer.trim()) consume(buffer);
    if (!result)
      throw new Error(
        "The connection ended before a final answer. Please retry.",
      );
    return result;
  } finally {
    reader.releaseLock();
  }
}
