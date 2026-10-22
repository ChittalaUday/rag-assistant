import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, streamChat } from "@/api/client";
import App from "./App";
vi.mock("@/features/trace/AnswerTrace", () => ({
  AnswerTrace: ({
    message,
    question,
    preview,
    onSource,
  }: {
    message: import("@/api/types").Message | null;
    question: string | null;
    preview: import("@/api/types").SearchPreview | null;
    onSource: (chunkId: string) => void;
  }) => (
    <div>
      <span>
        trace: {message?.content ?? "none"} · for: {question ?? "none"}
      </span>
      <span>
        likely:{" "}
        {preview ? preview.passages.map((p) => p.document).join(",") : "none"}
      </span>
      {["A", "B"].map((id) => (
        <button key={id} onClick={() => onSource(id)}>
          Open source {id}
        </button>
      ))}
    </div>
  ),
}));
vi.mock("@/api/client", () => ({
  api: {
    health: vi.fn(),
    modelsStatus: vi.fn(),
    answerModels: vi.fn(),
    loadModels: vi.fn(),
    documents: vi.fn(),
    documentContent: vi.fn(),
    preview: vi.fn(),
    graph: vi.fn(),
    languages: vi.fn(),
    conversations: vi.fn(),
    source: vi.fn(),
    upload: vi.fn(),
    conversation: vi.fn(),
  },
  streamChat: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
beforeEach(() => {
  vi.mocked(api.health).mockResolvedValue({
    status: "ok",
    ollama_available: false,
    chat_model: "local",
    embedding_model: "embed",
    models: [],
    warnings: [],
  });
  vi.mocked(api.modelsStatus).mockRejectedValue(new Error("not available"));
  vi.mocked(api.documents).mockResolvedValue({ documents: [] });
  vi.mocked(api.conversations).mockResolvedValue({ conversations: [] });
  vi.mocked(api.preview).mockResolvedValue({ passages: [], corrections: {} });
  vi.mocked(api.graph).mockResolvedValue({ entities: [], relations: [] });
  vi.mocked(api.answerModels).mockResolvedValue({ models: [], errors: [] });
  vi.mocked(api.languages).mockResolvedValue({
    languages: ["English", "Telugu"],
  });
});
const TRACE: import("@/api/types").AnswerTrace = {
  search_text: "q",
  rewritten: false,
  corrections: {},
  intent: "lookup",
  counts: { dense: 1, sparse: 1, candidates: 1 },
  reranked: false,
  bridge_terms: [],
  passages: [],
};
const reply = (answer: string, extra: object = {}) => ({
  conversation_id: "chat",
  answer,
  citations: [],
  statements: [],
  trace: TRACE,
  warnings: [],
  model: "local",
  ...extra,
});
it("shows offline recovery and an empty answer trace", async () => {
  render(<App />);
  expect(await screen.findByText(/Ollama is unavailable/)).toBeInTheDocument();
  expect(screen.getByText("trace: none · for: none")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Check connection" }),
  ).toBeEnabled();
});
it("renders Niruthi branding and public-documents affordances", async () => {
  render(<App />);
  expect(
    await screen.findByRole("link", {
      name: /Niruthi Company Assistant home/i,
    }),
  ).toBeInTheDocument();
  expect(screen.getByText(/Niruthi Climate & Ecosystems/i)).toBeInTheDocument();
  expect(
    screen.getByRole("button", {
      name: /Load Niruthi & fictional test documents/i,
    }),
  ).toBeInTheDocument();
  expect(
    screen.getAllByText(
      /load public Niruthi notes alongside clearly labeled fictional internal testing documents/i,
    ).length,
  ).toBeGreaterThan(0);
});
async function openSourceWith(citation: import("@/api/types").Citation) {
  vi.mocked(api.source).mockResolvedValue(citation);
  render(<App />);
  await screen.findByText(/Ollama is unavailable/);
  await userEvent.click(screen.getByRole("button", { name: "Open source A" }));
}
it("renders a safe Source website link when the citation text contains an http URL", async () => {
  await openSourceWith({
    chunk_id: "A",
    document_id: "A",
    document_name: "Doc A",
    text: "See https://example.org/report. End.",
    quote: null,
    page: null,
  });
  const link = await screen.findByRole("link", { name: /Source website/i });
  expect(link).toHaveAttribute("href", "https://example.org/report");
  expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  expect(link).toHaveAttribute("target", "_blank");
});
it("prefers a safe source_url over a URL in the citation text", async () => {
  await openSourceWith({
    chunk_id: "A",
    document_id: "A",
    document_name: "Doc A",
    text: "Fallback at https://fallback.example/x",
    quote: null,
    page: null,
    source_url: "https://preferred.example/a",
  });
  const link = await screen.findByRole("link", { name: /Source website/i });
  expect(link).toHaveAttribute("href", "https://preferred.example/a");
});
it("ignores unsafe source_url protocols and falls back to a URL in the text", async () => {
  await openSourceWith({
    chunk_id: "A",
    document_id: "A",
    document_name: "Doc A",
    text: "Fallback at http://example.com/x",
    quote: null,
    page: null,
    source_url: "javascript:alert(1)",
  });
  const link = await screen.findByRole("link", { name: /Source website/i });
  expect(link).toHaveAttribute("href", "http://example.com/x");
});
it("does not render a Source website link when no http URL is present and source_url is unsafe", async () => {
  await openSourceWith({
    chunk_id: "A",
    document_id: "A",
    document_name: "Doc A",
    text: "No URL here. Just prose.",
    quote: null,
    page: null,
    source_url: "file:///etc/passwd",
  });
  await screen.findByText(/No URL here/);
  expect(
    screen.queryByRole("link", { name: /Source website/i }),
  ).not.toBeInTheDocument();
});
it("surfaces upload failure and allows another upload", async () => {
  vi.mocked(api.upload).mockRejectedValue(
    new Error("Unsupported document format"),
  );
  const { container } = render(<App />);
  await screen.findByText(/Ollama is unavailable/);
  const input = container.querySelector("input[type=file]") as HTMLInputElement;
  await userEvent.upload(
    input,
    new File(["bad"], "report.txt", { type: "text/plain" }),
  );
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Unsupported document format",
    ),
  );
  expect(screen.getByRole("button", { name: "Upload document" })).toBeEnabled();
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
function historyFixture() {
  vi.mocked(api.conversations).mockResolvedValue({
    conversations: [
      { id: "a", title: "History A", created_at: "" },
      { id: "b", title: "History B", created_at: "" },
    ],
  });
}
it("keeps the newer history choice when requests finish in reverse order", async () => {
  historyFixture();
  const a = deferred<{
    id: string;
    messages: import("@/api/types").Message[];
  }>();
  const b = deferred<{
    id: string;
    messages: import("@/api/types").Message[];
  }>();
  vi.mocked(api.conversation).mockImplementation((id) =>
    id === "a" ? a.promise : b.promise,
  );
  render(<App />);
  await userEvent.click(
    await screen.findByRole("button", { name: "History A" }),
  );
  await userEvent.click(screen.getByRole("button", { name: "History B" }));
  await act(async () =>
    b.resolve({
      id: "b",
      messages: [{ role: "assistant", content: "Answer B" }],
    }),
  );
  await act(async () =>
    a.resolve({
      id: "a",
      messages: [{ role: "assistant", content: "Answer A" }],
    }),
  );
  expect(screen.getByText("Answer B")).toBeInTheDocument();
  expect(screen.queryByText("Answer A")).not.toBeInTheDocument();
});
it("invalidates pending history when starting a new conversation", async () => {
  historyFixture();
  const pending = deferred<{
    id: string;
    messages: import("@/api/types").Message[];
  }>();
  vi.mocked(api.conversation).mockReturnValue(pending.promise);
  render(<App />);
  await userEvent.click(
    await screen.findByRole("button", { name: "History A" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "New conversation" }),
  );
  await act(async () =>
    pending.resolve({
      id: "a",
      messages: [{ role: "assistant", content: "Old answer" }],
    }),
  );
  expect(screen.queryByText("Old answer")).not.toBeInTheDocument();
  expect(screen.getByText(/What would you like/)).toBeInTheDocument();
});
it("keeps the latest source and does not reopen its closed drawer", async () => {
  const a = deferred<import("@/api/types").Citation>();
  const b = deferred<import("@/api/types").Citation>();
  vi.mocked(api.source).mockImplementation((id) =>
    id === "A" ? a.promise : b.promise,
  );
  render(<App />);
  await screen.findByText(/Ollama is unavailable/);
  await userEvent.click(screen.getByRole("button", { name: "Open source A" }));
  await userEvent.click(screen.getByRole("button", { name: "Open source B" }));
  await act(async () =>
    b.resolve({
      chunk_id: "B",
      document_id: "B",
      document_name: "Source B",
      text: "Passage B",
      quote: null,
      page: null,
    }),
  );
  expect(screen.getByRole("dialog")).toHaveTextContent("Passage B");
  await userEvent.click(screen.getByRole("button", { name: "Close source" }));
  await act(async () =>
    a.resolve({
      chunk_id: "A",
      document_id: "A",
      document_name: "Source A",
      text: "Passage A",
      quote: null,
      page: null,
    }),
  );
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
});
it("does not replace the newer source when an older source finishes last", async () => {
  const a = deferred<import("@/api/types").Citation>();
  const b = deferred<import("@/api/types").Citation>();
  vi.mocked(api.source).mockImplementation((id) =>
    id === "A" ? a.promise : b.promise,
  );
  render(<App />);
  await screen.findByText(/Ollama is unavailable/);
  await userEvent.click(screen.getByRole("button", { name: "Open source A" }));
  await userEvent.click(screen.getByRole("button", { name: "Open source B" }));
  await act(async () =>
    b.resolve({
      chunk_id: "B",
      document_id: "B",
      document_name: "Source B",
      text: "Passage B",
      quote: null,
      page: null,
    }),
  );
  await act(async () =>
    a.resolve({
      chunk_id: "A",
      document_id: "A",
      document_name: "Source A",
      text: "Passage A",
      quote: null,
      page: null,
    }),
  );
  expect(screen.getByRole("dialog")).toHaveTextContent("Passage B");
  expect(screen.getByRole("dialog")).not.toHaveTextContent("Passage A");
});
it("invalidates pending source inspection when starting a new conversation", async () => {
  const pending = deferred<import("@/api/types").Citation>();
  vi.mocked(api.source).mockReturnValue(pending.promise);
  render(<App />);
  await screen.findByText(/Ollama is unavailable/);
  await userEvent.click(screen.getByRole("button", { name: "Open source A" }));
  await userEvent.click(
    screen.getByRole("button", { name: "New conversation" }),
  );
  await act(async () =>
    pending.resolve({
      chunk_id: "A",
      document_id: "A",
      document_name: "Source A",
      text: "Passage A",
      quote: null,
      page: null,
    }),
  );
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
});
it("shows each safe source_urls entry once and ignores unsafe entries", async () => {
  await openSourceWith({
    chunk_id: "A",
    document_id: "A",
    document_name: "Public snapshot",
    text: "Fallback https://fallback.example/report",
    quote: null,
    page: null,
    source_urls: [
      "https://niruthi.com/",
      "javascript:alert(1)",
      "https://example.org/report",
      "https://niruthi.com/",
    ],
  });
  const links = await screen.findAllByRole("link", { name: /Source website/i });
  expect(links).toHaveLength(2);
  expect(links[0]).toHaveAttribute("href", "https://niruthi.com/");
  expect(links[1]).toHaveAttribute("href", "https://example.org/report");
});
it("distinguishes public notes from fictional internal test documents", async () => {
  render(<App />);
  await screen.findByText(/Ollama is unavailable/);
  expect(
    screen.getByRole("button", {
      name: "Load Niruthi & fictional test documents",
    }),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/Fictional policies and roles are not Niruthi facts/),
  ).toBeInTheDocument();
});
it("sends distinct questions and displays authoritative answers and model warnings", async () => {
  vi.mocked(streamChat)
    .mockResolvedValueOnce(reply("First supported answer"))
    .mockResolvedValueOnce(
      reply("No supporting evidence", {
        warnings: ["Model supplied no valid citations"],
      }),
    );
  render(<App />);
  await screen.findByText(/Ollama is unavailable/);
  const input = screen.getByLabelText("Ask a question");
  await userEvent.type(input, "What does the public website describe?");
  await userEvent.click(screen.getByRole("button", { name: "Send question" }));
  await screen.findByText("First supported answer");
  await userEvent.type(input, "What is the internal leave policy?");
  await userEvent.click(screen.getByRole("button", { name: "Send question" }));
  await screen.findByText("No supporting evidence");
  expect(vi.mocked(streamChat).mock.calls[0][0]).toBe(
    "What does the public website describe?",
  );
  expect(vi.mocked(streamChat).mock.calls[1].slice(0, 2)).toEqual([
    "What is the internal leave policy?",
    "chat",
  ]);
  expect(
    screen.getByText("Model supplied no valid citations"),
  ).toBeInTheDocument();
});
it("shows backend conversation stages before displaying validated answer segments", async () => {
  const pending = deferred<import("@/api/types").ChatResponse>();
  let emitStatus!: (message: string) => void;
  let emitToken!: (text: string) => void;
  vi.mocked(streamChat).mockImplementation((_q, _id, onToken, onStatus) => {
    emitStatus = onStatus;
    emitToken = onToken;
    return pending.promise;
  });
  render(<App />);
  await screen.findByText(/Ollama is unavailable/);
  await userEvent.type(
    screen.getByLabelText("Ask a question"),
    "What changed?",
  );
  await userEvent.click(screen.getByRole("button", { name: "Send question" }));
  act(() => emitStatus("Generating an answer with the local model."));
  expect(
    screen.getByRole("status", { name: "Conversation progress" }),
  ).toHaveTextContent("Generating an answer with the local model.");
  act(() => emitToken("Validated segment"));
  expect(
    screen.getByRole("status", { name: "Conversation progress" }),
  ).toHaveTextContent("Displaying validated answer segments");
  await act(async () => pending.resolve(reply("Final answer")));
  expect(
    screen.queryByRole("status", { name: "Conversation progress" }),
  ).not.toBeInTheDocument();
});

it("streams the model's draft, marked unchecked, until the validated answer replaces it", async () => {
  const pending = deferred<import("@/api/types").ChatResponse>();
  let emitDraft!: (text: string) => void;
  let emitToken!: (text: string) => void;
  vi.mocked(streamChat).mockImplementation(
    (_q, _id, onToken, _onStatus, _signal, _labels, _onTrace, onDraft) => {
      emitDraft = onDraft!;
      emitToken = onToken;
      return pending.promise;
    },
  );
  render(<App />);
  await screen.findByText(/Ollama is unavailable/);
  await userEvent.type(screen.getByLabelText("Ask a question"), "Who?");
  await userEvent.click(screen.getByRole("button", { name: "Send question" }));
  act(() => emitDraft("Mira le"));
  act(() => emitDraft("Mira leads Acme"));
  expect(
    screen.getByText(/not yet checked against the sources/),
  ).toBeInTheDocument();
  // the conversation bubble (the mocked trace panel echoes the text too)
  expect(document.querySelector(".message.assistant")).toHaveTextContent(
    "Mira leads Acme",
  );
  act(() => emitToken("Validated."));
  expect(screen.queryByText(/not yet checked/)).not.toBeInTheDocument();
  expect(document.querySelector(".message.assistant")).toHaveTextContent(
    /^NIRUTHI ASSISTANT\s*Validated\./,
  );
  await act(async () => pending.resolve(reply("Validated.")));
});

it("suggested prompts populate the composer without fabricating an answer", async () => {
  render(<App />);
  await screen.findByText(/Ollama is unavailable/);
  await userEvent.click(
    screen.getByRole("button", { name: /Explore public information/i }),
  );
  expect(screen.getByLabelText("Ask a question")).toHaveValue(
    "What services does Niruthi describe in the public notes?",
  );
  expect(streamChat).not.toHaveBeenCalled();
});
it("lets the reader exclude synthetic documents and warns when sources conflict", async () => {
  const doc = (id: string, labels: string[]) => ({
    id,
    name: `${id}.md`,
    content_hash: id,
    status: "ready" as const,
    chunk_count: 1,
    created_at: "2026-10-08T00:00:00+00:00",
    error: null,
    warnings: [],
    labels,
  });
  vi.mocked(api.documents).mockResolvedValue({
    documents: [doc("real", []), doc("scenario", ["synthetic"])],
  });
  vi.mocked(streamChat).mockResolvedValue(
    reply("Anil or Bob", { status: "conflicting_evidence" }),
  );
  render(<App />);
  const toggle = await screen.findByLabelText(
    /Include fictional test documents/,
  );
  expect(toggle).toBeChecked();
  await userEvent.type(
    screen.getByLabelText("Ask a question"),
    "Who is the manager?",
  );
  await userEvent.click(screen.getByRole("button", { name: "Send question" }));
  await screen.findByText("Anil or Bob");
  expect(vi.mocked(streamChat).mock.calls[0][5]).toEqual([]);
  expect(screen.getByRole("note")).toHaveTextContent(/sources disagree/i);
  await userEvent.click(toggle);
  await userEvent.type(screen.getByLabelText("Ask a question"), "And now?");
  await userEvent.click(screen.getByRole("button", { name: "Send question" }));
  await vi.waitFor(() => expect(streamChat).toHaveBeenCalledTimes(2));
  expect(vi.mocked(streamChat).mock.calls[1][5]).toEqual(["synthetic"]);
});
it("traces the latest answer, and an earlier one when asked", async () => {
  vi.mocked(streamChat)
    .mockResolvedValueOnce(reply("First answer"))
    .mockResolvedValueOnce(reply("Second answer"));
  render(<App />);
  await screen.findByText(/Ollama is unavailable/);
  const input = screen.getByLabelText("Ask a question");
  await userEvent.type(input, "First question");
  await userEvent.click(screen.getByRole("button", { name: "Send question" }));
  await screen.findByText("First answer");
  expect(
    screen.getByText("trace: First answer · for: First question"),
  ).toBeInTheDocument();
  await userEvent.type(input, "Second question");
  await userEvent.click(screen.getByRole("button", { name: "Send question" }));
  await screen.findByText("Second answer");
  expect(
    screen.getByText("trace: Second answer · for: Second question"),
  ).toBeInTheDocument();
  const [first, second] = screen.getAllByRole("button", {
    name: /How this was answered/,
  });
  expect(second).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(first);
  expect(first).toHaveAttribute("aria-pressed", "true");
  expect(
    screen.getByText("trace: First answer · for: First question"),
  ).toBeInTheDocument();
});

it("switches between Chat and Documents sections in the sidebar using tabs", async () => {
  const doc = {
    id: "doc-1",
    name: "handbook.md",
    content_hash: "hash123",
    status: "ready" as const,
    chunk_count: 5,
    created_at: "2026-10-08T00:00:00+00:00",
    error: null,
    warnings: [],
    labels: [],
  };
  vi.mocked(api.documents).mockResolvedValue({ documents: [doc] });

  render(<App />);
  await screen.findByText(/Ollama is unavailable/);

  // Sidebar navigation tabs are present
  const chatTab = screen.getByRole("tab", { name: /^Chat/i });
  const docsTab = screen.getByRole("tab", { name: /^Documents/i });
  expect(chatTab).toBeInTheDocument();
  expect(docsTab).toBeInTheDocument();
  expect(chatTab).toHaveAttribute("aria-selected", "true");

  // In sidebar Chat tab: New conversation is present
  expect(
    screen.getByRole("button", { name: "New conversation" }),
  ).toBeInTheDocument();

  // Click Documents in sidebar
  await userEvent.click(docsTab);
  expect(docsTab).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("Document library")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Upload document" }),
  ).toBeInTheDocument();

  // Question composer is available in main workspace
  expect(screen.getByLabelText("Ask a question")).toBeInTheDocument();

  // Switch back to Chat
  await userEvent.click(chatTab);
  expect(chatTab).toHaveAttribute("aria-selected", "true");
});
it("lists the likely sources while a question is typed", async () => {
  vi.mocked(api.preview).mockResolvedValue({
    passages: [
      {
        chunk_id: "c1",
        document_id: "d1",
        document: "roster.md",
        heading: "Roster",
      },
    ],
    corrections: {},
  });
  render(<App />);
  await screen.findByText(/Ollama is unavailable/);
  const input = screen.getByLabelText("Ask a question");
  await userEvent.type(input, "who does Mira lead");
  expect(await screen.findByText("likely: roster.md")).toBeInTheDocument();
  expect(api.preview).toHaveBeenLastCalledWith(
    "who does Mira lead",
    expect.any(AbortSignal),
    [],
  );
  await userEvent.clear(input);
  await waitFor(() =>
    expect(screen.getByText("likely: none")).toBeInTheDocument(),
  );
});

it("renders assistant markdown with headings, tables, lists, and relative tags using Streamdown", async () => {
  const markdownAnswer = `### Policy Summary

Here is the table:

| Parameter | Value |
| --- | --- |
| Max Leave | 18 days |

- First item
- Second item

See [Keka PMS](#keka-pms) for details.`;

  vi.mocked(api.conversations).mockResolvedValue({
    conversations: [
      {
        id: "c-md",
        title: "Markdown Test",
        created_at: new Date().toISOString(),
      },
    ],
  });
  vi.mocked(api.conversation).mockResolvedValue({
    id: "c-md",
    messages: [
      { role: "user", content: "Format test" },
      {
        role: "assistant",
        content: markdownAnswer,
        citations: [],
        warnings: [],
        status: "answered",
        statements: [],
      },
    ],
  });

  render(<App />);
  await screen.findByText(/Ollama is unavailable/);

  const convButton = await screen.findByRole("button", {
    name: "Markdown Test",
  });
  await userEvent.click(convButton);

  expect(
    await screen.findByRole("heading", { name: "Policy Summary" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("table")).toBeInTheDocument();
  expect(screen.getByText("Max Leave")).toBeInTheDocument();
  expect(screen.getByText("First item")).toBeInTheDocument();
  const tagLink = screen.getByTitle("View Keka PMS");
  expect(tagLink).toBeInTheDocument();
  expect(tagLink).toHaveClass("relative-tag-pill");
});

it("shows the complete response time under each answer", async () => {
  const pending = deferred<import("@/api/types").ChatResponse>();
  vi.mocked(streamChat).mockReturnValue(pending.promise);
  render(<App />);
  await screen.findByText(/Ollama is unavailable/);
  await userEvent.type(screen.getByLabelText("Ask a question"), "Who?");
  await userEvent.click(screen.getByRole("button", { name: "Send question" }));
  expect(screen.queryByText(/Answered in/)).not.toBeInTheDocument();
  await act(async () => pending.resolve(reply("Mira leads Acme.")));
  // measured in the browser, from send to the final answer
  expect(screen.getByText(/^Answered in \d+\.\d s$/)).toBeInTheDocument();
});

it("shows the server's total time for answers loaded from history", async () => {
  historyFixture();
  vi.mocked(api.conversation).mockResolvedValue({
    id: "a",
    messages: [
      {
        role: "assistant",
        content: "Old answer",
        trace: {
          ...TRACE,
          timings: {
            understand_ms: 1,
            retrieve_ms: 1,
            answer_ms: 1,
            check_ms: 1,
            total_ms: 12345,
          },
        },
      },
    ],
  });
  render(<App />);
  await userEvent.click(
    await screen.findByRole("button", { name: "History A" }),
  );
  expect(await screen.findByText("Answered in 12.3 s")).toBeInTheDocument();
});

it("answers in the question's language unless an answer language is chosen", async () => {
  try {
    localStorage.removeItem("answer-language");
  } catch {
    /* no storage in this environment */
  }
  vi.mocked(streamChat).mockResolvedValue(reply("Answer"));
  render(<App />);
  await screen.findByText(/Ollama is unavailable/);
  const picker = await screen.findByRole("combobox", {
    name: "Answer language",
  });
  expect(picker).toHaveValue(""); // Auto
  const input = screen.getByLabelText("Ask a question");
  await userEvent.type(input, "Who?");
  await userEvent.click(screen.getByRole("button", { name: "Send question" }));
  await screen.findByText("Answer");
  expect(vi.mocked(streamChat).mock.calls[0][8]).toBeNull();
  await userEvent.selectOptions(picker, "Telugu");
  await userEvent.type(input, "Who again?");
  await userEvent.click(screen.getByRole("button", { name: "Send question" }));
  await waitFor(() => expect(streamChat).toHaveBeenCalledTimes(2));
  expect(vi.mocked(streamChat).mock.calls[1][8]).toBe("Telugu");
});

const modelState = (
  role: import("@/api/types").ModelRole,
  model: string,
  loaded: boolean,
  installed = true,
): import("@/api/types").ModelState => ({
  role,
  model,
  installed,
  loaded,
  state: !installed ? "not_installed" : loaded ? "loaded" : "not_loaded",
  where: "local",
  detail: null,
  loading_seconds: null,
  size_bytes: loaded ? 1_000_000 : null,
  expires_at: null,
});
const status = (
  ...models: import("@/api/types").ModelState[]
): import("@/api/types").ModelsStatus => ({
  ollama_available: true,
  models,
  error: null,
});
const bothLoaded = status(
  modelState("chat", "qwen3.5:9b", true),
  modelState("embedding", "embeddinggemma", true),
);
const chatCold = status(
  modelState("chat", "qwen3.5:9b", false),
  modelState("embedding", "embeddinggemma", true),
);
function forgetAutoLoad() {
  try {
    localStorage.removeItem("auto-load-models");
  } catch {
    /* no storage in this environment */
  }
}
async function typeQuestion(text: string) {
  await userEvent.type(screen.getByLabelText("Ask a question"), text);
}
const send = () =>
  userEvent.click(screen.getByRole("button", { name: "Send question" }));
it("shows which models are in memory and sends straight away when they are", async () => {
  forgetAutoLoad();
  vi.mocked(api.modelsStatus).mockResolvedValue(bothLoaded);
  vi.mocked(streamChat).mockResolvedValue(reply("Answer"));
  render(<App />);
  expect(await screen.findByText("Answers")).toBeInTheDocument();
  expect(screen.getAllByText("loaded")).toHaveLength(2);
  expect(screen.queryByRole("button", { name: "Load now" })).toBeNull();
  await typeQuestion("Who?");
  await send();
  await screen.findByText("Answer");
  expect(api.loadModels).not.toHaveBeenCalled();
});
it("holds a question until its model is loaded, then sends it", async () => {
  forgetAutoLoad();
  vi.mocked(api.modelsStatus).mockResolvedValue(chatCold);
  vi.mocked(api.loadModels).mockResolvedValue(bothLoaded);
  vi.mocked(streamChat).mockResolvedValue(reply("Loaded answer"));
  render(<App />);
  await screen.findByText("not loaded");
  await typeQuestion("Who leads?");
  await send();
  expect(streamChat).not.toHaveBeenCalled();
  expect(await screen.findByText(/to answer your question/)).toBeVisible();
  expect(screen.getByLabelText("Ask a question")).toHaveValue("Who leads?");
  await userEvent.click(screen.getByRole("button", { name: "Load and send" }));
  await screen.findByText("Loaded answer");
  expect(api.loadModels).toHaveBeenCalledTimes(1);
  expect(vi.mocked(streamChat).mock.calls[0][0]).toBe("Who leads?");
});
it("lets the question be cancelled instead of loading", async () => {
  forgetAutoLoad();
  vi.mocked(api.modelsStatus).mockResolvedValue(chatCold);
  render(<App />);
  await screen.findByText("not loaded");
  await typeQuestion("Who?");
  await send();
  await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
  expect(screen.queryByText(/to answer your question/)).toBeNull();
  expect(api.loadModels).not.toHaveBeenCalled();
  expect(streamChat).not.toHaveBeenCalled();
});
it("loads automatically when asked to, and remembers the choice", async () => {
  forgetAutoLoad();
  vi.mocked(api.modelsStatus).mockResolvedValue(chatCold);
  vi.mocked(api.loadModels).mockResolvedValue(bothLoaded);
  vi.mocked(streamChat).mockResolvedValue(reply("Auto answer"));
  render(<App />);
  await userEvent.click(
    await screen.findByRole("checkbox", { name: /Load models automatically/ }),
  );
  expect(localStorage.getItem("auto-load-models")).toBe("1");
  await typeQuestion("Who?");
  await send();
  await screen.findByText("Auto answer");
  expect(api.loadModels).toHaveBeenCalledWith(["chat"], null); // only what was cold
  forgetAutoLoad();
});
it("loads the models on request without a question", async () => {
  forgetAutoLoad();
  vi.mocked(api.modelsStatus).mockResolvedValue(chatCold);
  vi.mocked(api.loadModels).mockResolvedValue(bothLoaded);
  render(<App />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Load now" }),
  );
  await waitFor(() =>
    expect(api.loadModels).toHaveBeenCalledWith(undefined, null),
  );
  await waitFor(() => expect(screen.getAllByText("loaded")).toHaveLength(2));
  expect(streamChat).not.toHaveBeenCalled();
});
it("keeps the question and shows the reason when loading fails", async () => {
  forgetAutoLoad();
  vi.mocked(api.modelsStatus).mockResolvedValue(chatCold);
  vi.mocked(api.loadModels).mockRejectedValue(new Error("out of memory"));
  render(<App />);
  await screen.findByText("not loaded");
  await typeQuestion("Who?");
  await send();
  await userEvent.click(
    await screen.findByRole("button", { name: "Load and send" }),
  );
  expect(await screen.findByText(/out of memory/)).toBeInTheDocument();
  expect(streamChat).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Ask a question")).toHaveValue("Who?");
});
it("does not send a question whose model is not installed and says how to install it", async () => {
  forgetAutoLoad();
  vi.mocked(api.modelsStatus).mockResolvedValue(
    status(
      modelState("chat", "qwen3.5:9b", false, false),
      modelState("embedding", "embeddinggemma", true),
    ),
  );
  render(<App />);
  expect(await screen.findAllByText(/ollama pull qwen3.5:9b/)).not.toHaveLength(
    0,
  );
  await typeQuestion("Who?");
  await send();
  expect(streamChat).not.toHaveBeenCalled();
  expect(api.loadModels).not.toHaveBeenCalled();
});
it("stays quiet when the API has no model status", async () => {
  forgetAutoLoad();
  vi.mocked(streamChat).mockResolvedValue(reply("Plain answer"));
  render(<App />);
  await screen.findByText(/Ollama is unavailable/);
  expect(screen.queryByRole("region", { name: "Models" })).toBeNull();
  await typeQuestion("Who?");
  await send();
  await screen.findByText("Plain answer");
});
const serverChat = (
  state: import("@/api/types").ModelStateName,
  extra: Partial<import("@/api/types").ModelState> = {},
): import("@/api/types").ModelState => ({
  ...modelState("chat", "nvidia/Qwen3.6-35B-A3B-NVFP4", state === "loaded"),
  state,
  where: "server",
  installed: state !== "unreachable",
  ...extra,
});
it("shows a sleeping server model and wakes it before the question is sent", async () => {
  forgetAutoLoad();
  const asleep = status(
    serverChat("asleep"),
    modelState("embedding", "embeddinggemma", true),
  );
  vi.mocked(api.modelsStatus).mockResolvedValue(asleep);
  vi.mocked(api.loadModels).mockResolvedValue(
    status(
      serverChat("loaded"),
      modelState("embedding", "embeddinggemma", true),
    ),
  );
  vi.mocked(streamChat).mockResolvedValue(reply("Woken answer"));
  render(<App />);
  expect(await screen.findByText("asleep")).toBeInTheDocument();
  expect(
    screen.getByText(/asleep to free memory.*The first question would wait/),
  ).toBeInTheDocument();
  await typeQuestion("Who?");
  await send();
  await userEvent.click(
    await screen.findByRole("button", { name: "Wake and send" }),
  );
  await screen.findByText("Woken answer");
  expect(api.loadModels).toHaveBeenCalledTimes(1);
});
it("shows the server model loading into memory, with how long it has taken", async () => {
  forgetAutoLoad();
  vi.mocked(api.modelsStatus).mockResolvedValue(
    status(
      serverChat("loading", { loading_seconds: 42 }),
      modelState("embedding", "embeddinggemma", true),
    ),
  );
  render(<App />);
  expect(await screen.findByText("loading… 42 s")).toBeInTheDocument();
  expect(
    screen.getByRole("status", { name: "Loading models" }),
  ).toHaveTextContent(
    /Loading nvidia\/Qwen3\.6-35B-A3B-NVFP4 on the server into memory… 42 s/,
  );
});
it("does not send a question when the model server cannot be reached", async () => {
  forgetAutoLoad();
  vi.mocked(api.modelsStatus).mockResolvedValue(
    status(
      serverChat("unreachable", { detail: "model server unreachable: down" }),
      modelState("embedding", "embeddinggemma", true),
    ),
  );
  render(<App />);
  expect(
    await screen.findByText(/Cannot reach the model server/),
  ).toBeInTheDocument();
  await typeQuestion("Who?");
  await send();
  expect(streamChat).not.toHaveBeenCalled();
  expect(api.loadModels).not.toHaveBeenCalled();
});
