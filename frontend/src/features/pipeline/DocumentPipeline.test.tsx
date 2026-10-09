import type { ReactElement } from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import type { Document, DocumentChunks } from "@/api/types";
import { DocumentPipeline } from "./DocumentPipeline";

// jsdom cannot lay out a canvas: a stand-in renders our cards and wires node clicks
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({
    nodes,
    edges,
    nodeTypes,
    onNodeClick,
  }: Record<string, never>) => (
    <div>
      {(edges as { id: string; className: string }[]).map((e) => (
        <i key={e.id} data-edge={e.id} className={e.className} />
      ))}
      {(nodes as { id: string; type: string; data: unknown }[]).map((n) => {
        const Card = (
          nodeTypes as Record<string, (p: { data: unknown }) => ReactElement>
        )[n.type];
        return (
          <div
            key={n.id}
            className="react-flow__node"
            data-id={n.id}
            onClick={(e) =>
              (onNodeClick as (e: unknown, n: unknown) => void)(e, n)
            }
          >
            <Card data={n.data} />
          </div>
        );
      })}
    </div>
  ),
  Background: () => null,
  BackgroundVariant: { Dots: "dots" },
  Handle: () => null,
  MarkerType: { ArrowClosed: "arrowclosed" },
  Panel: () => null,
  Position: { Top: "top", Bottom: "bottom", Left: "left", Right: "right" },
  useReactFlow: () => ({
    zoomIn: vi.fn(),
    zoomOut: vi.fn(),
    fitView: vi.fn(),
    setCenter: vi.fn(),
    getZoom: () => 0.8,
  }),
  useViewport: () => ({ zoom: 0.8, x: 0, y: 0 }),
  useNodesState: (initial: unknown[]) => [initial, vi.fn(), vi.fn()],
}));
vi.mock("@/api/client", () => ({ api: { documentChunks: vi.fn() } }));

const doc = (over: Partial<Document> = {}): Document => ({
  id: "d1",
  name: "fictional-teams.md",
  content_hash: "abcdef0123456789",
  status: "ready",
  chunk_count: 2,
  created_at: "2026-10-09T10:00:00Z",
  error: null,
  warnings: [],
  pipeline_version: 5,
  processing_stage: "ready",
  processing_message: "Ready",
  processed_chunks: 2,
  total_chunks: 2,
  ...over,
});
const chunks = (d: Document, summary = ""): DocumentChunks => ({
  document: d,
  text_chars: 1200,
  chunks: [
    {
      id: "c1",
      position: 0,
      heading: "Teams",
      text: "Asha Rao leads Monsoon Lens.",
      location: "fictional teams › Teams",
      summary,
      char_start: 0,
      char_end: 600,
      page: null,
      embedding_model: "embeddinggemma",
    },
    {
      id: "c2",
      position: 1,
      heading: "Leave",
      text: "Leave needs approval. ".repeat(30),
      location: "fictional teams › Leave",
      summary: "",
      char_start: 580,
      char_end: 1200,
      page: null,
      embedding_model: "embeddinggemma",
    },
  ],
});

beforeEach(() => vi.mocked(api.documentChunks).mockReset());
afterEach(cleanup);

const card = (id: string) =>
  document.querySelector(`[data-id="${id}"] .pipe-card`)!;
const details = () => screen.getByRole("region", { name: /details$/ });

it("shows a ready document's path and its passages, filterable and expandable", async () => {
  const d = doc();
  vi.mocked(api.documentChunks).mockResolvedValue(chunks(d));
  render(
    <DocumentPipeline
      document={d}
      onClose={vi.fn()}
      onOpenDocument={vi.fn()}
    />,
  );
  expect(
    await screen.findByText("Asha Rao leads Monsoon Lens."),
  ).toBeInTheDocument();
  for (const id of [
    "checking",
    "dedup",
    "queued",
    "parsing",
    "embedding",
    "publishing",
    "ready",
  ])
    expect(card(id)).toHaveClass("done");
  expect(card("situating")).toHaveClass("skipped"); // summaries were off
  expect(card("failed")).toHaveClass("skipped");
  expect(card("parsing")).toHaveTextContent("1,200 characters · 2 passages");
  expect(card("embedding")).toHaveTextContent("2 vectors · embeddinggemma");
  // passages: filter, expand
  expect(screen.getByText("#2")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Filter passages"), {
    target: { value: "monsoon" },
  });
  expect(screen.queryByText("#2")).not.toBeInTheDocument();
  expect(screen.getByText("1 of 2")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Filter passages"), {
    target: { value: "" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Show the whole passage" }),
  );
  expect(screen.getByRole("button", { name: "Show less" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  // a state's details
  fireEvent.click(document.querySelector('[data-id="situating"]')!);
  expect(details()).toHaveAccessibleName("Situating details");
  expect(
    within(details()).getByText(/contextual summaries were off/),
  ).toBeInTheDocument();
});

it("follows a document being processed, with the running stage's progress", async () => {
  const d = doc({
    status: "processing",
    processing_stage: "embedding",
    processed_chunks: 12,
    total_chunks: 40,
    chunk_count: 0,
  });
  vi.mocked(api.documentChunks).mockResolvedValue({
    document: d,
    text_chars: 0,
    chunks: [],
  });
  render(
    <DocumentPipeline
      document={d}
      onClose={vi.fn()}
      onOpenDocument={vi.fn()}
    />,
  );
  expect(
    await screen.findByText(
      "Passages appear here once the document is published.",
    ),
  ).toBeInTheDocument();
  expect(card("parsing")).toHaveClass("done");
  expect(card("embedding")).toHaveClass("active");
  expect(card("embedding")).toHaveTextContent("12 of 40 passages");
  expect(card("publishing")).toHaveClass("pending");
  expect(details()).toHaveAccessibleName("Embedding details");
});

it("shows a failed document's error", async () => {
  const d = doc({
    status: "failed",
    error: "Document contains no readable text",
    chunk_count: 0,
  });
  vi.mocked(api.documentChunks).mockResolvedValue({
    document: d,
    text_chars: 0,
    chunks: [],
  });
  render(
    <DocumentPipeline
      document={d}
      onClose={vi.fn()}
      onOpenDocument={vi.fn()}
    />,
  );
  expect(
    await screen.findByText(
      "Passages appear here once the document is published.",
    ),
  ).toBeInTheDocument();
  expect(card("failed")).toHaveClass("failed");
  expect(card("parsing")).toHaveClass("skipped");
  expect(
    within(details()).getByText("Document contains no readable text"),
  ).toBeInTheDocument();
});

it("draws the summary path only when the passages were summarised", async () => {
  const d = doc();
  vi.mocked(api.documentChunks).mockResolvedValue(
    chunks(d, "Lists the team leads."),
  );
  render(
    <DocumentPipeline
      document={d}
      onClose={vi.fn()}
      onOpenDocument={vi.fn()}
    />,
  );
  expect(await screen.findByText("Lists the team leads.")).toBeInTheDocument();
  expect(card("situating")).toHaveClass("done");
  expect(card("situating")).toHaveTextContent("1 of 2 summarised");
  expect(document.querySelector('[data-edge="parsing-situating"]')).toHaveClass(
    "taken",
  );
  expect(
    document.querySelector('[data-edge="parsing-embedding"]'),
  ).not.toHaveClass("taken");
});
