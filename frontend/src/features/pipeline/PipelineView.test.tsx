import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, expect, it, vi } from "vitest";
import type { Message } from "@/api/types";
import { PipelineView } from "./PipelineView";

// jsdom cannot lay out a canvas: a stand-in renders our cards and wires node clicks
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({
    nodes,
    edges,
    nodeTypes,
    onNodeClick,
    children,
  }: Record<string, never>) => (
    <div data-testid="canvas">
      {(nodes as { id: string; type: string; data: unknown }[]).map((n) => {
        const Card = (
          nodeTypes as Record<string, (p: { data: unknown }) => ReactElement>
        )[n.type];
        return (
          <div
            key={n.id}
            className="react-flow__node"
            data-id={n.id}
            tabIndex={0}
            onClick={(e) =>
              (onNodeClick as (e: unknown, n: unknown) => void)(e, n)
            }
          >
            <Card data={n.data} />
          </div>
        );
      })}
      <ul data-testid="edges">
        {(edges as { id: string; className: string; animated: boolean }[]).map(
          (e) => (
            <li
              key={e.id}
              data-edge={e.id}
              className={e.className}
              data-animated={String(e.animated)}
            />
          ),
        )}
      </ul>
      {children}
    </div>
  ),
  Background: () => null,
  BackgroundVariant: { Dots: "dots" },
  Handle: () => null,
  MarkerType: { ArrowClosed: "arrowclosed" },
  Panel: ({ children }: { children: unknown }) => (
    <div>{children as never}</div>
  ),
  Position: { Top: "top", Bottom: "bottom", Left: "left", Right: "right" },
  useReactFlow: () => ({
    zoomIn: vi.fn(),
    zoomOut: vi.fn(),
    fitView: vi.fn(),
    setCenter: vi.fn(),
    getZoom: () => 0.8,
  }),
  useViewport: () => ({ zoom: 0.75, x: 0, y: 0 }),
  useNodesState: (initial: unknown[]) => [initial, vi.fn(), vi.fn()],
}));

afterEach(cleanup);

const trace = {
  search_text: "who leads acme",
  rewritten: false,
  corrections: { acem: "acme" },
  intent: "lookup",
  counts: { dense: 12, sparse: 4, candidates: 14 },
  reranked: true,
  bridge_terms: ["mira"],
  passages: [
    {
      chunk_id: "c1",
      document_id: "d1",
      document: "roster.md",
      heading: "Team",
      score: 0.9,
      ranks: { dense: 1 },
    },
    {
      chunk_id: "c2",
      document_id: "d2",
      document: "org.md",
      heading: null,
      score: 0.5,
      ranks: { sparse: 1 },
    },
  ],
  timings: {
    understand_ms: 20,
    retrieve_ms: 3000,
    answer_ms: 4000,
    check_ms: 3000,
    total_ms: 10000,
  },
} as unknown as NonNullable<Message["trace"]>;

const answered: Message = {
  role: "assistant",
  content: "Mira leads Acme.",
  status: "answered",
  citations: [
    {
      chunk_id: "c1",
      document_id: "d1",
      document_name: "roster.md",
      text: "Mira leads Acme.",
      quote: null,
      page: null,
    },
  ],
  warnings: [],
  statements: [
    { text: "Mira leads Acme.", sources: ["c1"] },
    { text: "She started in 2020.", sources: [] },
  ],
  trace,
  metrics: {
    route: "documents",
    route_score: -0.21,
    first_draft_ms: 3500,
    retries: ["wrong_language"],
    stages_ms: {
      routing: 20,
      retrieving: 10,
      embedding_query: 30,
      searching: 40,
      reranking: 2900,
      generating: 4000,
      validating: 5,
      verifying: 3000,
    },
    usage: {
      generate: {
        calls: 2,
        prompt_tokens: 3600,
        output_tokens: 110,
        model_ms: 7000,
        load_ms: 2,
      },
      rerank: {
        calls: 30,
        prompt_tokens: 7900,
        output_tokens: 30,
        model_ms: 11000,
        load_ms: 30,
      },
    },
  },
};

const card = (id: string) =>
  document.querySelector(`[data-id="${id}"] .pipe-card`)!;
const statusTile = () => document.querySelector(".pipe-tile:last-child strong");
const details = () => screen.getByRole("region", { name: /details$/ });

it("draws the path a cited answer took, with tiles, times and the slowest state open", () => {
  render(
    <PipelineView message={answered} question="Who leads Acme?" live={null} />,
  );
  expect(screen.getByText("10.0 s").closest(".pipe-tile")).toHaveTextContent(
    "Elapsed time",
  );
  expect(screen.getByText("2/14")).toBeInTheDocument(); // passages used/found
  expect(statusTile()).toHaveTextContent("Finished");
  expect(card("reranking")).toHaveClass("done");
  expect(card("reranking")).toHaveTextContent("RANK");
  expect(card("reranking")).toHaveTextContent("2.9 s · complete");
  expect(card("replying")).toHaveClass("skipped");
  expect(card("noinfo")).toHaveClass("skipped");
  expect(card("generating")).toHaveTextContent("retried ×1");
  expect(
    document.querySelector('[data-edge="validating-verifying"]'),
  ).toHaveClass("taken");
  expect(
    document.querySelector('[data-edge="routing-replying"]'),
  ).not.toHaveClass("taken");
  // the slowest state (generating, 4.0 s) is open by default
  expect(details()).toHaveAccessibleName("Generating details");
  expect(within(details()).getByText("wrong language")).toBeInTheDocument();
});

it("opens a state's details when it is selected, by click or keyboard", () => {
  render(
    <PipelineView message={answered} question="Who leads Acme?" live={null} />,
  );
  fireEvent.click(document.querySelector('[data-id="evidence"]')!);
  expect(details()).toHaveAccessibleName("Choosing evidence details");
  expect(within(details()).getByText("2 from 2 documents")).toBeInTheDocument();
  expect(within(details()).getByText("mira")).toBeInTheDocument();
  expect(
    within(details()).getByText(
      "backend/knowledge_engine/retrieval/hop.py, retrieval/structure.py, reasoning/evidence.py",
    ),
  ).toBeInTheDocument();
  fireEvent.keyDown(document.querySelector('[data-id="verifying"]')!, {
    key: "Enter",
  });
  expect(details()).toHaveAccessibleName("Verifying details");
  expect(
    within(details()).getByText("1 of 2 stated by a passage"),
  ).toBeInTheDocument();
  expect(
    within(details()).getByText("“She started in 2020.”"),
  ).toBeInTheDocument();
  expect(within(details()).getByText(/30% of the answer/)).toBeInTheDocument();
});

it("shows small talk going straight from routing to the reply", () => {
  const reply: Message = {
    role: "assistant",
    content: "Hello! Ask me about your documents.",
    status: "conversation",
    metrics: {
      route: "small_talk",
      route_score: 0.41,
      stages_ms: { routing: 20, replying: 1100 },
    },
  };
  render(<PipelineView message={reply} question="hello" live={null} />);
  expect(card("replying")).toHaveClass("done");
  for (const id of [
    "understanding",
    "searching",
    "reranking",
    "generating",
    "verifying",
    "noinfo",
  ])
    expect(card(id)).toHaveClass("skipped");
  expect(statusTile()).toHaveTextContent("Small talk");
  expect(document.querySelector('[data-edge="replying-done"]')).toHaveClass(
    "taken",
  );
  fireEvent.click(document.querySelector('[data-id="routing"]')!);
  expect(within(details()).getByText(/0\.410/)).toBeInTheDocument();
});

it("follows a live answer: running state highlighted, its edge animated", () => {
  const t0 = 1_000_000;
  const inflight: Message = {
    role: "assistant",
    content: "Mira le",
    drafting: true,
    trace,
  };
  render(
    <PipelineView
      message={inflight}
      question="Who leads Acme?"
      live={{
        startedAt: t0,
        now: t0 + 6000,
        stages: [
          { stage: "routing", at: t0 },
          { stage: "retrieving", at: t0 + 30 },
          { stage: "embedding_query", at: t0 + 60 },
          { stage: "searching", at: t0 + 100 },
          { stage: "reranking", at: t0 + 150 },
          { stage: "generating", at: t0 + 3000 },
        ],
      }}
    />,
  );
  expect(statusTile()).toHaveTextContent("Running");
  expect(card("generating")).toHaveClass("active");
  expect(card("generating")).toHaveTextContent("3.0 s · running");
  expect(card("verifying")).toHaveClass("pending");
  expect(
    document.querySelector('[data-edge="evidence-generating"]'),
  ).toHaveAttribute("data-animated", "true");
  expect(details()).toHaveAccessibleName("Generating details");
  expect(
    within(details()).getByText("7 characters so far"),
  ).toBeInTheDocument();
});

it("shows an answer the documents could not support ending in no information", () => {
  const none: Message = {
    role: "assistant",
    content: "I couldn't find information about that.",
    status: "insufficient_evidence",
    trace: {
      ...trace,
      passages: [],
      counts: { dense: 0, sparse: 0, candidates: 0 },
    },
  };
  render(
    <PipelineView
      message={none}
      question="What is the parking policy?"
      live={null}
    />,
  );
  expect(card("noinfo")).toHaveClass("done");
  expect(card("verifying")).toHaveClass("skipped");
  expect(statusTile()).toHaveTextContent("No information");
});
