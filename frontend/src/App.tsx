import { useCallback, useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import * as Tabs from "@radix-ui/react-tabs";
import {
  ArrowUp,
  BookOpen,
  FileText,
  MessageSquare,
  Plus,
  RefreshCw,
  Trash2,
  Upload,
  X,
  PanelLeft,
  LoaderCircle,
} from "lucide-react";
import { api, streamChat } from "./api";
import { GraphMap } from "./GraphMap";
import { emptyGraph } from "./types";
import type {
  Citation,
  Conversation,
  Document,
  Health,
  KnowledgeGraph,
  Message,
} from "./types";
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : "An unexpected error occurred";
function safeHttpUrl(input: string | null | undefined): string | null {
  if (!input) return null;
  try {
    const url = new URL(input.trim());
    if (url.protocol === "http:" || url.protocol === "https:")
      return url.toString();
  } catch {
    /* not a URL */
  }
  return null;
}
function sourceWebsites(citation: Citation | null): string[] {
  if (!citation) return [];
  const provided = [...(citation.source_urls ?? []), citation.source_url]
    .map(safeHttpUrl)
    .filter((url): url is string => url !== null);
  if (provided.length) return [...new Set(provided)];
  const matches =
    citation.text.match(/\bhttps?:\/\/[^\s<>()[\]{}"'`]+/gi) ?? [];
  return [
    ...new Set(
      matches
        .map((match) => safeHttpUrl(match.replace(/[.,;:!?)\]}'"]+$/, "")))
        .filter((url): url is string => url !== null),
    ),
  ];
}
export default function App() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [graph, setGraph] = useState<KnowledgeGraph>(emptyGraph);
  const [health, setHealth] = useState<Health | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [evidence, setEvidence] = useState<Citation | null>(null);
  const [mobileTab, setMobileTab] = useState("chat");
  const [sidebar, setSidebar] = useState(false);
  const uploadInput = useRef<HTMLInputElement>(null);
  const replaceInput = useRef<HTMLInputElement>(null);
  const replacing = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const historyRequest = useRef(0);
  const sourceRequest = useRef(0);
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const refresh = useCallback(async () => {
    const [docs, map, model, history] = await Promise.all([
      api.documents(),
      api.graph(),
      api.health(),
      api.conversations(),
    ]);
    setDocuments(docs.documents);
    setHealth(model);
    setConversations(history.conversations);
    setGraph((current) => {
      const next = {
        ...map,
        highlighted_node_ids: current.highlighted_node_ids.filter((id) =>
          map.nodes.some((n) => n.id === id),
        ),
        highlighted_edge_ids: current.highlighted_edge_ids.filter((id) =>
          map.edges.some((e) => e.id === id),
        ),
      };
      return JSON.stringify(current) === JSON.stringify(next) ? current : next;
    });
  }, []);
  useEffect(() => {
    void refresh().catch((e) =>
      setError(`Cannot connect to the local API. ${errorText(e)}`),
    );
    const timer = setInterval(() => {
      void refresh().catch((e) => setError(errorText(e)));
    }, 4000);
    return () => {
      clearInterval(timer);
      abort.current?.abort();
      historyRequest.current += 1;
      sourceRequest.current += 1;
    };
  }, [refresh]);
  useEffect(() => {
    end.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [messages, status]);
  const selectEvidence = useCallback((citation: Citation) => {
    const requestId = ++sourceRequest.current;
    setError("");
    void api
      .source(citation.chunk_id)
      .then((source) => {
        if (requestId !== sourceRequest.current) return;
        setEvidence({
          ...source,
          quote:
            citation.quote && source.text.includes(citation.quote)
              ? citation.quote
              : source.quote,
        });
      })
      .catch((e) => {
        if (requestId !== sourceRequest.current) return;
        setEvidence(null);
        setError(`This source is no longer available. ${errorText(e)}`);
      });
  }, []);
  async function action(work: () => Promise<unknown>) {
    setError("");
    setUploading(true);
    try {
      await work();
      await refresh();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setUploading(false);
    }
  }
  async function send() {
    const text = question.trim();
    if (!text || busy) return;
    historyRequest.current += 1;
    setQuestion("");
    setError("");
    setBusy(true);
    setStatus("Finding supporting passages…");
    const original = messagesRef.current;
    setMessages([
      ...original,
      { role: "user", content: text },
      { role: "assistant", content: "" },
    ]);
    abort.current = new AbortController();
    try {
      const result = await streamChat(
        text,
        conversationId,
        (token) =>
          setMessages((current) =>
            current.map((m, i) =>
              i === current.length - 1
                ? { ...m, content: m.content + token }
                : m,
            ),
          ),
        setStatus,
        abort.current.signal,
      );
      setConversationId(result.conversation_id);
      setMessages([
        ...original,
        { role: "user", content: text },
        {
          role: "assistant",
          content: result.answer,
          citations: result.citations,
          graph: result.graph,
          warnings: result.warnings,
        },
      ]);
      setGraph(result.graph);
      setConversations((await api.conversations()).conversations);
    } catch (e) {
      setMessages([...original, { role: "user", content: text }]);
      setQuestion(text);
      setError(errorText(e));
    } finally {
      setBusy(false);
      setStatus("");
    }
  }
  async function openConversation(id: string) {
    if (busy) return;
    const requestId = ++historyRequest.current;
    sourceRequest.current += 1;
    setEvidence(null);
    setError("");
    try {
      const conversation = await api.conversation(id);
      if (requestId !== historyRequest.current) return;
      const latest = [...conversation.messages]
        .reverse()
        .find((m) => m.graph)?.graph;
      const live = await api.graph();
      if (requestId !== historyRequest.current) return;
      setConversationId(id);
      setMessages(conversation.messages);
      setGraph({
        ...live,
        highlighted_node_ids: (latest?.highlighted_node_ids ?? []).filter(
          (id) => live.nodes.some((n) => n.id === id),
        ),
        highlighted_edge_ids: (latest?.highlighted_edge_ids ?? []).filter(
          (id) => live.edges.some((e) => e.id === id),
        ),
      });
      setSidebar(false);
    } catch (e) {
      if (requestId === historyRequest.current) setError(errorText(e));
    }
  }
  function newChat() {
    if (busy) return;
    historyRequest.current += 1;
    sourceRequest.current += 1;
    setEvidence(null);
    setConversationId(null);
    setMessages([]);
    setQuestion("");
    setGraph((current) => ({
      ...current,
      highlighted_node_ids: [],
      highlighted_edge_ids: [],
    }));
    setSidebar(false);
  }
  const ready = documents.filter((d) => d.status === "ready").length;
  const pending = documents.filter(
    (d) => d.status === "queued" || d.status === "processing",
  ).length;
  return (
    <div className="workspace">
      <aside className={`sidebar ${sidebar ? "open" : ""}`}>
        <a
          className="brand"
          href="/"
          aria-label="Niruthi Company Assistant home"
        >
          <div className="brand-mark">
            <BookOpen size={20} />
          </div>
          <span>
            Niruthi
            <br />
            <strong>Company Assistant</strong>
          </span>
        </a>
        <div className="workspace-label">LOCAL WORKSPACE</div>
        <button className="new-chat" onClick={newChat} disabled={busy}>
          <Plus size={16} /> New conversation
        </button>
        <div className="nav-label">
          Conversations <MessageSquare size={13} />
        </div>
        <nav className="history" aria-label="Conversation history">
          {conversations.length ? (
            conversations.map((c) => (
              <div
                className={`history-item ${conversationId === c.id ? "active" : ""}`}
                key={c.id}
              >
                <button
                  onClick={() => void openConversation(c.id)}
                  disabled={busy}
                >
                  {c.title}
                </button>
                <button
                  aria-label={`Delete conversation ${c.title}`}
                  disabled={busy}
                  onClick={() =>
                    void action(async () => {
                      await api.deleteConversation(c.id);
                      if (conversationId === c.id) newChat();
                    })
                  }
                >
                  <Trash2 size={12} />
                </button>
              </div>
            ))
          ) : (
            <p className="muted small">Your conversations will appear here.</p>
          )}
        </nav>
        <div className="nav-label">
          Documents <span>{documents.length}</span>
        </div>
        <div className="document-list">
          {documents.map((d) => (
            <div className="document" key={d.id}>
              <FileText size={15} />
              <div className="document-body">
                <strong title={d.name}>{d.name}</strong>
                <span className={`doc-status ${d.status}`}>
                  {d.status === "processing" ? (
                    <LoaderCircle size={11} className="spin" />
                  ) : null}
                  {d.status}{" "}
                  {d.status === "ready" ? `· ${d.chunk_count} chunks` : ""}
                </span>
                {d.error && <p className="doc-error">{d.error}</p>}
                {d.warnings.map((w, i) => (
                  <p key={i} className="doc-warning">
                    {w}
                  </p>
                ))}
                <div className="doc-actions">
                  <button
                    aria-label={`${d.status === "failed" ? "Retry" : "Replace"} ${d.name}`}
                    onClick={() => {
                      replacing.current = d.id;
                      replaceInput.current?.click();
                    }}
                    disabled={uploading}
                  >
                    <RefreshCw size={12} />
                    {d.status === "failed" ? "Retry" : "Replace"}
                  </button>
                  <button
                    aria-label={`Delete document ${d.name}`}
                    onClick={() => void action(() => api.deleteDocument(d.id))}
                    disabled={uploading}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
        <input
          ref={uploadInput}
          type="file"
          accept=".txt,.md,.pdf,.docx"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void action(() => api.upload(file));
            e.target.value = "";
          }}
        />
        <input
          ref={replaceInput}
          type="file"
          accept=".txt,.md,.pdf,.docx"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            const id = replacing.current;
            if (file && id) void action(() => api.replace(id, file));
            e.target.value = "";
          }}
        />
        <button
          className="upload"
          onClick={() => uploadInput.current?.click()}
          disabled={uploading}
        >
          <Upload size={15} />
          {uploading ? "Updating documents…" : "Upload document"}
        </button>
        <button
          className="demo"
          onClick={() => void action(() => api.demo())}
          disabled={uploading}
        >
          Load Niruthi public documents
        </button>
        <p className="file-note">
          Public-source snapshot, not internal policies.
        </p>
        <p className="file-note">TXT, Markdown, PDF, DOCX · up to 20 MB</p>
        <div className="local-status">
          <i className={health?.ollama_available ? "online" : ""} />
          <span>
            {health?.ollama_available
              ? "Local models connected"
              : "Local models offline"}
            <small>Documents stay on this computer</small>
          </span>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div className="topbar-title">
            <button
              className="sidebar-toggle"
              aria-label="Toggle navigation"
              onClick={() => setSidebar(!sidebar)}
            >
              <PanelLeft size={19} />
            </button>
            <span>Niruthi Climate &amp; Ecosystems</span>
            <span className="topbar-slash">/</span>
            <strong>
              {conversationId ? "Conversation" : "New conversation"}
            </strong>
          </div>
          <span className="topbar-state">
            {pending ? `${pending} processing` : `${ready} documents ready`}
          </span>
        </header>
        {error && (
          <div role="alert" className="error-banner">
            <span>{error}</span>
            <button aria-label="Dismiss error" onClick={() => setError("")}>
              <X size={16} />
            </button>
          </div>
        )}
        {health && !health.ollama_available && (
          <div className="offline-banner">
            Ollama is unavailable. Start Ollama and install the configured
            models to process documents and ask questions.
            <button
              onClick={() =>
                void refresh().catch((e) => setError(errorText(e)))
              }
            >
              Check connection
            </button>
          </div>
        )}
        {health?.warnings.map((warning, index) => (
          <div className="warning-banner" key={index}>
            {warning}
          </div>
        ))}
        <Tabs.Root
          className="panels"
          value={mobileTab}
          onValueChange={setMobileTab}
        >
          <Tabs.List className="mobile-tabs" aria-label="Workspace panels">
            <Tabs.Trigger value="chat">Conversation</Tabs.Trigger>
            <Tabs.Trigger value="map">Knowledge map</Tabs.Trigger>
          </Tabs.List>
          <Tabs.Content value="chat" forceMount className="chat-panel">
            <header className="panel-header">
              <div>
                <div className="eyebrow">ASK YOUR DOCUMENTS</div>
                <h1>Answers with evidence.</h1>
              </div>
              <span className="small muted">Local inference</span>
            </header>
            <div className="messages" aria-live="polite">
              {!messages.length && (
                <div className="chat-empty">
                  <div className="empty-icon">
                    <MessageSquare size={22} />
                  </div>
                  <h2>
                    What would you like
                    <br />
                    to understand?
                  </h2>
                  <p>
                    Ask about your company’s projects, responsibilities, or
                    decisions. Each answer connects back to the documents behind
                    it.
                  </p>
                  <div className="empty-note">
                    <FileText size={16} />
                    <span>
                      {documents.length
                        ? "Your documents are listed in the sidebar. Processing must finish before their passages can be used."
                        : "Start by uploading company documents or loading Niruthi public documents — a public-source snapshot, not internal policies."}
                    </span>
                  </div>
                </div>
              )}
              {messages.map((message, index) => (
                <article className={`message ${message.role}`} key={index}>
                  <div className="message-author">
                    {message.role === "user" ? "YOU" : "NIRUTHI ASSISTANT"}
                  </div>
                  <div className="answer-text">
                    {message.content || (
                      <span className="muted">
                        {status || "Preparing answer…"}
                      </span>
                    )}
                  </div>
                  {Boolean(message.citations?.length) && (
                    <div className="citations">
                      <div className="eyebrow">SOURCES</div>
                      {message.citations?.map((citation, i) => (
                        <button
                          key={`${citation.chunk_id}-${i}`}
                          onClick={() => selectEvidence(citation)}
                        >
                          <span>{i + 1}</span>
                          <FileText size={13} />
                          {citation.document_name}
                          {citation.page ? ` · p. ${citation.page}` : ""}
                        </button>
                      ))}
                    </div>
                  )}
                  {message.warnings?.map((w, i) => (
                    <p className="answer-warning" key={i}>
                      {w}
                    </p>
                  ))}
                </article>
              ))}
              <div ref={end} />
            </div>
            <form
              className="composer"
              onSubmit={(e) => {
                e.preventDefault();
                void send();
              }}
            >
              <label htmlFor="question" className="sr-only">
                Ask a question
              </label>
              <textarea
                id="question"
                rows={2}
                placeholder="Ask a question about your documents…"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
                disabled={busy}
              />
              <div className="composer-bottom">
                <span>
                  {busy ? status : "Grounded in your documents · Enter to send"}
                </span>
                <button
                  type="submit"
                  aria-label="Send question"
                  disabled={busy || !question.trim()}
                >
                  {busy ? (
                    <LoaderCircle className="spin" size={18} />
                  ) : (
                    <ArrowUp size={18} />
                  )}
                </button>
              </div>
            </form>
            <p className="composer-note">
              Inspect sources to verify an answer. Unsupported questions may
              receive no answer.
            </p>
          </Tabs.Content>
          <Tabs.Content value="map" forceMount className="map-tab">
            <GraphMap graph={graph} onEvidence={selectEvidence} />
          </Tabs.Content>
        </Tabs.Root>
      </main>
      <Dialog.Root
        open={Boolean(evidence)}
        onOpenChange={(open) => {
          if (!open) {
            sourceRequest.current += 1;
            setEvidence(null);
          }
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="drawer-overlay" />
          <Dialog.Content className="source-drawer">
            <div className="drawer-header">
              <div className="eyebrow">SOURCE EVIDENCE</div>
              <Dialog.Close aria-label="Close source">
                <X size={20} />
              </Dialog.Close>
            </div>
            <Dialog.Title>{evidence?.document_name}</Dialog.Title>
            <Dialog.Description className="muted">
              {evidence?.page ? `Page ${evidence.page} · ` : ""}Source chunk{" "}
              {evidence?.chunk_id}
            </Dialog.Description>
            <div className="source-websites">
              {sourceWebsites(evidence).map((url, index, urls) => (
                <a
                  key={url}
                  className="source-link"
                  href={url}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  Source website
                  {urls.length > 1
                    ? ` ${index + 1} · ${new URL(url).hostname}`
                    : ""}
                </a>
              ))}
            </div>
            {evidence?.quote && (
              <>
                <h3>Exact supporting passage</h3>
                <blockquote>{evidence.quote}</blockquote>
              </>
            )}
            <h3>Source context</h3>
            <pre>{evidence?.text}</pre>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
