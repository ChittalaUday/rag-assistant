import { useCallback, useEffect, useRef, useState } from "react";
import { useReducedMotion } from "motion/react";
import {
  Dialog,
  DialogContent,
  DialogClose,
  DialogTitle,
  DialogDescription,
} from "./components/animate-ui/components/radix/dialog";
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "./components/animate-ui/components/radix/tabs";
import { Button } from "./components/animate-ui/components/buttons/button";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "./components/animate-ui/components/radix/tooltip";

import {
  ArrowUp,
  FileText,
  MessageSquare,
  Plus,
  RefreshCw,
  Trash2,
  Upload,
  X,
  PanelLeft,
  LoaderCircle,
  Leaf,
  ShieldCheck,
  ArrowUpRight,
  Users,
  Route,
} from "lucide-react";
import { api, streamChat } from "@/api/client";
import { DocumentProgress } from "@/features/documents/Progress";
import { DocumentReader } from "@/features/documents/DocumentReader";
import { AnswerTrace } from "@/features/trace/AnswerTrace";
import type {
  Citation,
  Conversation,
  Document,
  Health,
  Message,
  SearchPreview,
} from "@/api/types";
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
  const reducedMotion = useReducedMotion();
  const [documents, setDocuments] = useState<Document[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [preview, setPreview] = useState<SearchPreview | null>(null);
  // index of the assistant message whose trace is shown; null: the latest answer
  const [traced, setTraced] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [evidence, setEvidence] = useState<Citation | null>(null);
  const [sidebarTab, setSidebarTab] = useState<"chat" | "documents">("chat");
  const [mobileTab, setMobileTab] = useState("chat");
  const [includeSynthetic, setIncludeSynthetic] = useState(true);
  const [sidebar, setSidebar] = useState(false);
  const [reader, setReader] = useState<{ id: string; name: string } | null>(
    null,
  );
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
    const [docs, model, history] = await Promise.all([
      api.documents(),
      api.health(),
      api.conversations(),
    ]);
    setDocuments(docs.documents);
    setHealth(model);
    setConversations(history.conversations);
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
    end.current?.scrollIntoView?.({
      behavior: reducedMotion ? "auto" : "smooth",
    });
  }, [messages, status, reducedMotion]);
  // While a question is typed, show the passages it is likely to draw on (no model call).
  useEffect(() => {
    const text = question.trim();
    if (text.length < 3) {
      setPreview(null);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api
        .preview(text, controller.signal, includeSynthetic ? [] : ["synthetic"])
        .then(setPreview)
        .catch(() => undefined);
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [question, includeSynthetic]);
  const selectEvidence = useCallback((chunkId: string) => {
    const requestId = ++sourceRequest.current;
    setError("");
    void api
      .source(chunkId)
      .then((source) => {
        if (requestId !== sourceRequest.current) return;
        setEvidence(source);
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
    setStatus("Sending your question to the local API…");
    const original = messagesRef.current;
    setMessages([
      ...original,
      { role: "user", content: text },
      { role: "assistant", content: "" },
    ]);
    setTraced(null);
    abort.current = new AbortController();
    try {
      const result = await streamChat(
        text,
        conversationId,
        (token) => {
          setStatus("Displaying validated answer segments…");
          setMessages((current) =>
            current.map((m, i) =>
              i === current.length - 1
                ? { ...m, content: m.content + token }
                : m,
            ),
          );
        },
        setStatus,
        abort.current.signal,
        includeSynthetic ? [] : ["synthetic"],
      );
      setConversationId(result.conversation_id);
      setMessages([
        ...original,
        { role: "user", content: text },
        {
          role: "assistant",
          content: result.answer,
          citations: result.citations,
          warnings: result.warnings,
          status: result.status,
          statements: result.statements,
          trace: result.trace,
        },
      ]);
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
      setConversationId(id);
      setMessages(conversation.messages);
      setTraced(null);
      setSidebarTab("chat");
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
    setTraced(null);
    setSidebarTab("chat");
    setSidebar(false);
  }
  const readerDocument = documents.find(
    (document) => document.id === reader?.id,
  );
  const readerRevision = readerDocument
    ? JSON.stringify([
        readerDocument.content_hash,
        readerDocument.status,
        readerDocument.processing_stage,
        readerDocument.processing_message,
        readerDocument.processed_chunks,
        readerDocument.total_chunks,
        readerDocument.chunk_count,
        readerDocument.error,
      ])
    : "";
  const answers = messages.flatMap((m, i) =>
    m.role === "assistant" && m.content ? [i] : [],
  );
  const shownIndex =
    traced !== null && messages[traced] ? traced : (answers.at(-1) ?? null);
  const shownAnswer = shownIndex !== null ? messages[shownIndex] : null;
  const shownQuestion =
    shownIndex !== null && messages[shownIndex - 1]?.role === "user"
      ? messages[shownIndex - 1].content
      : null;
  const typing = question.trim().length >= 3 ? preview : null;
  const pending = documents.filter(
    (d) => d.status === "queued" || d.status === "processing",
  ).length;
  return (
    <div className="workspace">
      <aside className={`sidebar ${sidebar ? "open" : ""}`}>
        <Button
          variant="ghost"
          size="icon-sm"
          className="sidebar-close"
          aria-label="Close navigation"
          onClick={() => setSidebar(false)}
        >
          <X size={17} />
        </Button>
        <a
          className="brand"
          href="/"
          aria-label="Niruthi Company Assistant home"
        >
          <div className="brand-mark">
            <Leaf size={22} />
          </div>
          <span>
            Niruthi
            <br />
            <strong>Company Assistant</strong>
          </span>
        </a>
        <div
          className="sidebar-tab-switcher"
          role="tablist"
          aria-label="Sidebar sections"
        >
          <button
            type="button"
            role="tab"
            aria-selected={sidebarTab === "chat"}
            className={`sidebar-tab-btn ${sidebarTab === "chat" ? "active" : ""}`}
            onClick={() => setSidebarTab("chat")}
          >
            <MessageSquare size={13} />
            <span>Chat</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={sidebarTab === "documents"}
            className={`sidebar-tab-btn ${sidebarTab === "documents" ? "active" : ""}`}
            onClick={() => setSidebarTab("documents")}
          >
            <FileText size={13} />
            <span>Documents</span>
            <span className="sidebar-tab-count">{documents.length}</span>
          </button>
        </div>

        <div
          className={`sidebar-section ${sidebarTab === "chat" ? "active" : "hidden"}`}
        >
          <Button
            variant="default"
            className="new-chat"
            onClick={newChat}
            disabled={busy}
          >
            <Plus size={16} /> New conversation
          </Button>
          <div className="nav-label">
            Recent conversations <MessageSquare size={13} />
          </div>
          <nav className="history" aria-label="Conversation history">
            {conversations.length ? (
              conversations.map((c) => (
                <div
                  className={`history-item ${conversationId === c.id ? "active" : ""}`}
                  key={c.id}
                >
                  <Button
                    variant="ghost"
                    onClick={() => void openConversation(c.id)}
                    disabled={busy}
                  >
                    {c.title}
                  </Button>
                  <Button
                    variant="ghost"
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
                  </Button>
                </div>
              ))
            ) : (
              <p className="history-empty">
                Start a conversation to build your history.
              </p>
            )}
          </nav>
        </div>

        <div
          className={`sidebar-section ${sidebarTab === "documents" ? "active" : "hidden"}`}
        >
          <div className="nav-label document-heading">
            <span>Document library</span>
            <span className="library-count">{documents.length}</span>
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
                  <DocumentProgress document={d} />
                  {d.error && <p className="doc-error">{d.error}</p>}
                  {d.warnings.map((w, i) => (
                    <p key={i} className="doc-warning">
                      {w}
                    </p>
                  ))}
                  <div className="doc-actions">
                    <Button
                      variant="ghost"
                      aria-label={`Open document ${d.name}`}
                      onClick={() => setReader({ id: d.id, name: d.name })}
                    >
                      Open document
                    </Button>
                    <Button
                      variant="ghost"
                      aria-label={`${d.status === "failed" ? "Retry" : "Replace"} ${d.name}`}
                      onClick={() => {
                        replacing.current = d.id;
                        replaceInput.current?.click();
                      }}
                      disabled={uploading}
                    >
                      <RefreshCw size={12} />
                      {d.status === "failed" ? "Retry" : "Replace"}
                    </Button>
                    <Button
                      variant="ghost"
                      aria-label={`Delete document ${d.name}`}
                      onClick={() =>
                        void action(() => api.deleteDocument(d.id))
                      }
                      disabled={uploading}
                    >
                      <Trash2 size={12} />
                    </Button>
                  </div>
                </div>
              </div>
            ))}
          </div>
          <Button
            variant="ghost"
            className="upload"
            onClick={() => uploadInput.current?.click()}
            disabled={uploading}
          >
            <Upload size={15} />
            {uploading ? "Updating documents…" : "Upload document"}
          </Button>
          <Button
            variant="ghost"
            className="demo"
            onClick={() => void action(() => api.demo())}
            disabled={uploading}
          >
            Load Niruthi & fictional test documents
          </Button>
          {documents.some((d) => d.labels?.includes("synthetic")) && (
            <label className="scope-toggle">
              <input
                type="checkbox"
                checked={includeSynthetic}
                onChange={(e) => setIncludeSynthetic(e.target.checked)}
              />
              Include fictional test documents in answers
            </label>
          )}
          <p className="file-note">TXT, Markdown, PDF, DOCX · up to 20 MB</p>
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
      </aside>
      <main>
        <header className="topbar">
          <div className="topbar-title">
            <Button
              variant="ghost"
              className="sidebar-toggle"
              aria-label="Toggle navigation"
              onClick={() => setSidebar(!sidebar)}
            >
              <PanelLeft size={19} />
            </Button>
            <div className="workspace-title">
              <span>Niruthi Climate &amp; Ecosystems</span>
              <small>Company knowledge, connected.</small>
            </div>
          </div>
          {pending > 0 && (
            <span className="topbar-state">
              <span className="status-dot" />
              {pending} processing
            </span>
          )}
        </header>
        {error && (
          <div role="alert" className="error-banner">
            <span>{error}</span>
            <Button
              variant="ghost"
              aria-label="Dismiss error"
              onClick={() => setError("")}
            >
              <X size={16} />
            </Button>
          </div>
        )}
        {health && !health.ollama_available && (
          <div className="offline-banner">
            Ollama is unavailable. Start Ollama and install the configured
            models to process documents and ask questions.
            <Button
              variant="ghost"
              onClick={() =>
                void refresh().catch((e) => setError(errorText(e)))
              }
            >
              Check connection
            </Button>
          </div>
        )}
        {health?.warnings.map((warning, index) => (
          <div className="warning-banner" key={index}>
            {warning}
          </div>
        ))}
        <Tabs className="panels" value={mobileTab} onValueChange={setMobileTab}>
          <TabsList className="mobile-tabs" aria-label="Workspace panels">
            <TabsTrigger value="chat">Conversation</TabsTrigger>
            <TabsTrigger value="trace">Answer trace</TabsTrigger>
          </TabsList>
          <TabsContent value="chat" forceMount className="chat-panel">
            <header className="panel-header">
              <div>
                <div className="eyebrow">ASK YOUR DOCUMENTS</div>
                <h1>Answers with evidence.</h1>
              </div>
              <span className="inference-badge">
                <ShieldCheck size={12} /> Local inference
              </span>
            </header>
            <div className="messages" aria-live="polite">
              {!messages.length && (
                <div className="chat-empty">
                  <div className="empty-icon">
                    <Leaf size={28} />
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
                  <div
                    className="prompt-cards"
                    aria-label="Suggested questions"
                  >
                    {[
                      {
                        icon: Leaf,
                        title: "Explore public information",
                        question:
                          "What services does Niruthi describe in the public notes?",
                      },
                      {
                        icon: Users,
                        title: "Find who is responsible",
                        question:
                          "Who approves leave requests in the fictional test documents?",
                      },
                      {
                        icon: FileText,
                        title: "Understand a policy",
                        question:
                          "What policies are described in the documents, and which are fictional test policies?",
                      },
                    ].map((prompt) => (
                      <Button
                        variant="outline"
                        className="prompt-card"
                        key={prompt.title}
                        onClick={() => setQuestion(prompt.question)}
                      >
                        <prompt.icon size={17} />
                        <span>{prompt.title}</span>
                        <ArrowUpRight size={14} />
                      </Button>
                    ))}
                  </div>
                  <div className="empty-note">
                    <FileText size={16} />
                    <span>
                      {documents.length
                        ? "Your documents are listed in the sidebar. Processing must finish before their passages can be used."
                        : "Upload your own documents or load public Niruthi notes alongside clearly labeled fictional internal testing documents. Fictional policies and roles are not Niruthi facts."}
                    </span>
                  </div>
                </div>
              )}
              {messages.map((message, index) => (
                <article
                  className={`message ${message.role} ${index === shownIndex ? "traced" : ""}`}
                  key={index}
                >
                  <div className="message-author">
                    {message.role === "user" ? "YOU" : "NIRUTHI ASSISTANT"}
                  </div>
                  <div className="answer-text">
                    {message.content ? (
                      <>
                        <span>{message.content}</span>
                        {busy && index === messages.length - 1 && (
                          <>
                            <span
                              className="streaming-cursor"
                              aria-hidden="true"
                            />
                            <span
                              className="sr-only"
                              role="status"
                              aria-label="Conversation progress"
                            >
                              {status}
                            </span>
                          </>
                        )}
                      </>
                    ) : busy && index === messages.length - 1 ? (
                      <div
                        className="shimmer-loading"
                        role="status"
                        aria-label="Conversation progress"
                      >
                        <span className="shimmer-pulse" />
                        <span className="shimmer-text">
                          {status || "Connecting sources and generating…"}
                        </span>
                      </div>
                    ) : (
                      <span className="muted">No response</span>
                    )}
                  </div>
                  {Boolean(message.citations?.length) && (
                    <div className="citations">
                      <div className="eyebrow">SOURCES</div>
                      {message.citations?.map((citation, i) => (
                        <Button
                          variant="ghost"
                          key={`${citation.chunk_id}-${i}`}
                          onClick={() => selectEvidence(citation.chunk_id)}
                        >
                          <span>{i + 1}</span>
                          <FileText size={13} />
                          {citation.document_name}
                          {citation.page ? ` · p. ${citation.page}` : ""}
                        </Button>
                      ))}
                    </div>
                  )}
                  {message.status === "conflicting_evidence" && (
                    <p className="answer-warning" role="note">
                      The sources disagree on this. Compare the cited passages
                      before relying on the answer.
                    </p>
                  )}
                  {message.warnings?.map((w, i) => (
                    <p className="answer-warning" key={i}>
                      {w}
                    </p>
                  ))}
                  {message.role === "assistant" && message.trace && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="trace-toggle"
                      aria-pressed={index === shownIndex}
                      onClick={() => {
                        setTraced(index);
                        setMobileTab("trace");
                      }}
                    >
                      <Route size={13} /> How this was answered
                    </Button>
                  )}
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
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="default"
                      type="submit"
                      aria-label="Send question"
                      disabled={busy || !question.trim()}
                    >
                      {busy ? (
                        <LoaderCircle className="spin" size={18} />
                      ) : (
                        <ArrowUp size={18} />
                      )}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Send question · Enter</TooltipContent>
                </Tooltip>
              </div>
            </form>
            <p className="composer-note">
              Inspect sources to verify an answer. Unsupported questions may
              receive no answer.
            </p>
          </TabsContent>
          <TabsContent value="trace" forceMount className="map-tab">
            <AnswerTrace
              message={shownAnswer}
              question={shownQuestion}
              preview={typing}
              onSource={selectEvidence}
            />
          </TabsContent>
        </Tabs>
      </main>
      <Dialog
        open={Boolean(evidence)}
        onOpenChange={(open) => {
          if (!open) {
            sourceRequest.current += 1;
            setEvidence(null);
          }
        }}
      >
        <DialogContent className="source-drawer" showCloseButton={false}>
          <div className="drawer-header">
            <div className="eyebrow">SOURCE EVIDENCE</div>
            <DialogClose asChild>
              <Button variant="ghost" size="icon" aria-label="Close source">
                <X size={20} />
              </Button>
            </DialogClose>
          </div>
          <DialogTitle>{evidence?.document_name}</DialogTitle>
          <DialogDescription className="muted">
            {evidence?.page ? `Page ${evidence.page} · ` : ""}Source chunk{" "}
            {evidence?.chunk_id}
          </DialogDescription>
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
          {evidence && (
            <Button
              variant="ghost"
              className="read-full"
              onClick={() => {
                sourceRequest.current += 1;
                setReader({
                  id: evidence.document_id,
                  name: evidence.document_name,
                });
                setEvidence(null);
              }}
            >
              Read full document
            </Button>
          )}
          <h3>Source context</h3>
          <pre>{evidence?.text}</pre>
        </DialogContent>
      </Dialog>
      <DocumentReader
        id={reader?.id ?? null}
        name={reader?.name ?? "Document"}
        revision={readerRevision}
        onClose={() => setReader(null)}
      />
    </div>
  );
}
