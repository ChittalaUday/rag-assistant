import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type Graph from "graphology";
import type { PreviewGraph } from "@/api/types";
import { KnowledgeGraph } from "./KnowledgeGraph";

type Reducer = (
  key: string,
  attrs: Record<string, unknown>,
) => Record<string, unknown>;
// jsdom has no WebGL: a stand-in renderer records its data, handlers and reducers
const drawn = vi.hoisted(() => ({
  graph: null as Graph | null,
  on: {} as Record<string, (payload: Record<string, unknown>) => void>,
  settings: {} as Record<string, Reducer>,
  fail: false,
}));
vi.mock("sigma", () => ({
  default: class {
    constructor(graph: Graph) {
      if (drawn.fail) throw new Error("no WebGL");
      drawn.graph = graph;
    }
    on(event: string, handler: (payload: Record<string, unknown>) => void) {
      drawn.on[event] = handler;
    }
    setSetting(name: string, value: Reducer) {
      drawn.settings[name] = value;
    }
    setCustomBBox() {}
    getCustomBBox() {
      return null;
    }
    graphToViewport(point: { x: number; y: number }) {
      return point;
    }
    viewportToFramedGraph(point: { x: number; y: number }) {
      return point;
    }
    getCamera() {
      return { animate: async () => {}, animatedReset: async () => {} };
    }
    kill() {}
  },
}));

afterEach(() => {
  cleanup();
  drawn.fail = false;
});

const relation = (
  source: string,
  label: string,
  target: string,
  chunk: string,
) => ({
  source,
  target,
  label,
  chunk_id: chunk,
  document: "roster.md",
  quote: `${source} ${label} ${target}.`,
});
const entity = (id: string, name: string) => ({
  id,
  name,
  typed: false,
  found: true,
});
const full: PreviewGraph = {
  entities: [
    entity("deputy ceo arun balla", "Deputy CEO Arun Balla"),
    entity("maria lindqvist", "Maria Lindqvist"),
    entity("zara quill", "Zara Quill"),
    entity("omar vance", "Omar Vance"),
  ],
  relations: [
    relation("deputy ceo arun balla", "reports to", "maria lindqvist", "c1"),
    relation("zara quill", "mentors", "omar vance", "c2"),
  ],
};
const live: PreviewGraph = {
  entities: [
    { id: "arun balla", name: "Arun Balla", typed: true, found: true },
    { id: "zed corp", name: "Zed Corp", typed: true, found: false },
  ],
  relations: [full.relations[0]],
};

it("draws the whole graph and opens the passage of a clicked relation", async () => {
  const onSource = vi.fn();
  render(
    <KnowledgeGraph full={full} live={null} text={null} onSource={onSource} />,
  );
  await waitFor(() => expect(drawn.graph?.order).toBe(4));
  expect(drawn.graph!.size).toBe(2);
  expect(screen.getByText("2 in all documents")).toBeInTheDocument();
  act(() => drawn.on.clickEdge({ edge: drawn.graph!.edges()[1] }));
  expect(onSource).toHaveBeenCalledWith("c2");
});

it("selects the names recognised in the typed text and lists their live relations", async () => {
  render(
    <KnowledgeGraph
      full={full}
      live={live}
      text="who is Arun Balla"
      onSource={vi.fn()}
    />,
  );
  expect(screen.getByText("Arun Balla")).toBeInTheDocument();
  expect(screen.getByText(/not in documents/)).toBeInTheDocument();
  const look = (id: string) =>
    drawn.settings.nodeReducer(id, { size: 5, color: "x" });
  await waitFor(() =>
    expect(look("deputy ceo arun balla")).toMatchObject({ highlighted: true }),
  );
  expect(look("maria lindqvist")).toMatchObject({ forceLabel: true }); // its neighbour
  expect(look("zara quill")).toMatchObject({ label: "" }); // dimmed
  expect(screen.getByText("1 for your question")).toBeInTheDocument();
  expect(screen.queryByText(/mentors/)).not.toBeInTheDocument();
});

it("focuses a clicked name's relations", async () => {
  render(
    <KnowledgeGraph full={full} live={null} text={null} onSource={vi.fn()} />,
  );
  await waitFor(() => expect(drawn.on.clickNode).toBeDefined());
  act(() => drawn.on.clickNode({ node: "zara quill" }));
  expect(screen.getByText("1 of Zara Quill")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "show all" }));
  expect(screen.getByText("2 in all documents")).toBeInTheDocument();
});

it("lists every relation when the browser cannot draw the graph", async () => {
  drawn.fail = true;
  render(
    <KnowledgeGraph full={full} live={null} text={null} onSource={vi.fn()} />,
  );
  expect(await screen.findByText(/needs WebGL/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /mentors/ })).toBeInTheDocument();
});

it("shows one language at a time, following the question unless one is chosen", async () => {
  const te = {
    ...relation("niruthi", "మరియు", "fieldbridge", "t1"),
    script: "TELUGU",
  };
  const mixed: PreviewGraph = {
    entities: [
      ...full.entities,
      entity("niruthi", "Niruthi"),
      entity("fieldbridge", "FieldBridge"),
    ],
    relations: [...full.relations.map((r) => ({ ...r, script: "LATIN" })), te],
  };
  const { rerender } = render(
    <KnowledgeGraph full={mixed} live={null} text={null} onSource={vi.fn()} />,
  );
  // the language most relations are in, until something is typed
  await waitFor(() => expect(drawn.graph?.size).toBe(2));
  expect(screen.getByText("2 in all documents")).toBeInTheDocument();
  rerender(
    <KnowledgeGraph
      full={mixed}
      live={null}
      text="నిరుతి ఏమి చేస్తుంది"
      onSource={vi.fn()}
    />,
  );
  await waitFor(() => expect(drawn.graph?.size).toBe(1));
  expect(screen.getByRole("button", { name: /మరియు/ })).toBeInTheDocument();
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Language" }),
    "LATIN",
  );
  await waitFor(() => expect(drawn.graph?.size).toBe(2));
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: /మరియు/ }),
    ).not.toBeInTheDocument(),
  ); // after its exit animation
});
