import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "./api";
import App from "./App";
vi.mock("./GraphMap", () => ({
  GraphMap: ({
    onEvidence,
    graph,
  }: {
    graph: import("./types").KnowledgeGraph;
    onEvidence: (source: import("./types").Citation) => void;
  }) => (
    <div>
      <span>
        {graph.nodes.length} entities · {graph.edges.length} relations
      </span>
      {["A", "B"].map((id) => (
        <button
          key={id}
          onClick={() =>
            onEvidence({
              chunk_id: id,
              document_id: id,
              document_name: `Source ${id}`,
              text: `Passage ${id}`,
              quote: null,
              page: null,
            })
          }
        >
          Open source {id}
        </button>
      ))}
    </div>
  ),
}));
vi.mock("./api", () => ({
  api: {
    health: vi.fn(),
    documents: vi.fn(),
    graph: vi.fn(),
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
  vi.mocked(api.documents).mockResolvedValue({ documents: [] });
  vi.mocked(api.graph).mockResolvedValue({
    nodes: [],
    edges: [],
    highlighted_edge_ids: [],
    highlighted_node_ids: [],
  });
  vi.mocked(api.conversations).mockResolvedValue({ conversations: [] });
});
it("shows offline recovery and empty graph without fabricated entities", async () => {
  render(<App />);
  expect(await screen.findByText(/Ollama is unavailable/)).toBeInTheDocument();
  expect(screen.getByText("0 entities · 0 relations")).toBeInTheDocument();
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
    screen.getByRole("button", { name: /Load Niruthi public documents/i }),
  ).toBeInTheDocument();
  expect(
    screen.getAllByText(/Public-source snapshot, not internal policies/i)
      .length,
  ).toBeGreaterThan(0);
  expect(
    screen.getByText(/Documents stay on this computer/i),
  ).toBeInTheDocument();
});
async function openSourceWith(citation: import("./types").Citation) {
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
  const a = deferred<{ id: string; messages: import("./types").Message[] }>();
  const b = deferred<{ id: string; messages: import("./types").Message[] }>();
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
    messages: import("./types").Message[];
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
  const a = deferred<import("./types").Citation>();
  const b = deferred<import("./types").Citation>();
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
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("does not replace the newer source when an older source finishes last", async () => {
  const a = deferred<import("./types").Citation>();
  const b = deferred<import("./types").Citation>();
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
  const pending = deferred<import("./types").Citation>();
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
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
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
