/* Shared pieces of the state-machine canvases (the answer pipeline and the document pipeline):
   state cards, transitions, a pannable and zoomable React Flow canvas, its controls. */
import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";
import {
  Background,
  BackgroundVariant,
  Handle,
  MarkerType,
  Panel,
  Position,
  ReactFlow,
  useNodesState,
  useReactFlow,
  useViewport,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Check, CircleAlert, LoaderCircle } from "lucide-react";

export type Status = "done" | "active" | "pending" | "skipped" | "failed";
export type Point = { x: number; y: number };
export const NODE_W = 210;

export type CardData = {
  kind: string; // the label above the title
  label: string;
  status: Status;
  sub: string; // time and state, under the title
  share: number | null; // of the total time, 0..1; null hides the bar
  note?: string; // a short highlighted remark after the sub line
  selected: boolean;
};

/** A transition, with the card sides it leaves and enters (see HANDLES). */
export type Transition<Id extends string> = {
  from: Id;
  to: Id;
  label?: string;
};

const CARD_H = 80; // about a card's height, for its centre

/** Which card sides an edge leaves and enters, from where the two states sit: sideways when the
 *  target is more beside than above or below, else through the top or bottom. */
function sides(a: Point, b: Point): { out: string; in: string } {
  const dx = b.x - a.x,
    dy = b.y - a.y;
  if (Math.abs(dx) >= Math.abs(dy) * 0.6)
    return dx >= 0 ? { out: "sr", in: "l" } : { out: "sl", in: "r" };
  return dy >= 0 ? { out: "b", in: "t" } : { out: "st", in: "tb" };
}

export const secs = (ms: number) =>
  `${(ms / 1000).toFixed(ms < 1000 ? 2 : 1)} s`;

/** The sub line of a card: time with complete or running, or the state. */
export function subLine(status: Status, ms: number | undefined): string {
  if (status === "active")
    return ms !== undefined ? `${secs(ms)} · running` : "running";
  if (status === "skipped") return "not taken";
  if (status === "pending") return "waiting";
  if (status === "failed") return "failed";
  return ms !== undefined ? `${secs(ms)} · complete` : "complete";
}

const CHIP: Record<Status, ReactNode> = {
  done: (
    <>
      <Check size={11} /> Done
    </>
  ),
  active: (
    <>
      <LoaderCircle size={11} className="spin" /> Running
    </>
  ),
  pending: "Waiting",
  skipped: "Not taken",
  failed: (
    <>
      <CircleAlert size={11} /> Failed
    </>
  ),
};

// connection points on every card, in and out on every side (an edge picks its sides from the layout)
const HANDLES: ["target" | "source", Position, string][] = [
  ["target", Position.Top, "t"],
  ["target", Position.Left, "l"],
  ["target", Position.Bottom, "tb"],
  ["target", Position.Right, "r"],
  ["source", Position.Right, "sr"],
  ["source", Position.Top, "st"],
  ["source", Position.Bottom, "b"],
  ["source", Position.Left, "sl"],
];

/** One state on the canvas: type label, status chip, title, time and share of the total. */
function StateCard({ data }: NodeProps<Node<CardData>>) {
  return (
    <div
      className={`pipe-card ${data.status} ${data.selected ? "selected" : ""}`}
      style={{ width: NODE_W }}
    >
      {HANDLES.map(([type, side, id]) => (
        <Handle
          key={id}
          type={type}
          position={side}
          id={id}
          isConnectable={false}
        />
      ))}
      <div className="pipe-card-top">
        <span className="pipe-kind">
          <i /> {data.kind}
        </span>
        <span className={`pipe-chip ${data.status}`}>{CHIP[data.status]}</span>
      </div>
      <div className="pipe-card-title">{data.label}</div>
      <div className="pipe-card-sub">
        {data.sub}
        {data.note && <span className="pipe-retry"> · {data.note}</span>}
      </div>
      {data.share !== null && (
        <span className="pipe-card-bar" aria-hidden="true">
          <i style={{ width: `${Math.max(2, data.share * 100)}%` }} />
        </span>
      )}
    </div>
  );
}
const NODE_TYPES = { state: StateCard };

/** Edges for the transitions: green where the path ran, blue and animated into the running
 *  state, grey where it did not. */
export function buildEdges<Id extends string>(
  transitions: Transition<Id>[],
  status: Record<Id, Status>,
  position: (id: Id) => Point,
): Edge[] {
  const ran = (s: Status) => s === "done" || s === "active" || s === "failed";
  return transitions.map((t) => {
    const taken = ran(status[t.from]) && ran(status[t.to]);
    const into = status[t.to] === "active";
    const a = position(t.from),
      b = position(t.to);
    const side = sides(
      { x: a.x + NODE_W / 2, y: a.y + CARD_H / 2 },
      { x: b.x + NODE_W / 2, y: b.y + CARD_H / 2 },
    );
    return {
      id: `${t.from}-${t.to}`,
      source: t.from,
      target: t.to,
      sourceHandle: side.out,
      targetHandle: side.in,
      label: t.label,
      animated: into,
      className: taken
        ? into
          ? "pipe-edge running"
          : status[t.to] === "failed"
            ? "pipe-edge failed"
            : "pipe-edge taken"
        : "pipe-edge",
      markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
    };
  });
}

/** While something runs, glides the view to the running state; when it ends, shows it all. */
function Follow({ point }: { point: Point | undefined }) {
  const { setCenter, getZoom, fitView } = useReactFlow();
  const was = useRef<Point | undefined>(undefined);
  const key = point ? `${point.x},${point.y}` : "";
  useEffect(() => {
    if (point) {
      void setCenter(point.x + NODE_W / 2, point.y + 40, {
        zoom: Math.max(getZoom(), 0.7),
        duration: 400,
      });
    } else if (was.current) {
      void fitView({ padding: 0.08, duration: 400 });
    }
    was.current = point;
    // keyed on the position, not the object: a new object with the same place is no move
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, setCenter, getZoom, fitView]);
  return null;
}

function CanvasControls() {
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  const { zoom } = useViewport();
  return (
    <>
      <Panel position="top-right" className="pipe-zoom-label">
        {Math.round(zoom * 100)}%
      </Panel>
      <Panel position="bottom-left" className="pipe-controls">
        <button
          type="button"
          aria-label="Zoom out"
          onClick={() => void zoomOut({ duration: 150 })}
        >
          −
        </button>
        <button
          type="button"
          onClick={() => void fitView({ padding: 0.08, duration: 200 })}
        >
          Fit
        </button>
        <button
          type="button"
          aria-label="Zoom in"
          onClick={() => void zoomIn({ duration: 150 })}
        >
          +
        </button>
      </Panel>
    </>
  );
}

/** A pannable, zoomable state machine. It keeps its own copy of the cards so the sizes React Flow
 *  measured survive live updates (which replace only their data); a focused card is selected
 *  with Enter or Space. */
export function StateCanvas({
  cards,
  edges,
  focus,
  onPick,
  label,
}: {
  cards: Node<CardData>[];
  edges: Edge[];
  focus: Point | undefined; // the running state's position, followed while set
  onPick: (id: string) => void;
  label: string;
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState(cards);
  useEffect(() => {
    setNodes((current) =>
      cards.map((card) => {
        const kept = current.find((n) => n.id === card.id);
        return kept
          ? { ...kept, data: card.data, ariaLabel: card.ariaLabel }
          : card;
      }),
    );
  }, [cards, setNodes]);
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const id = (e.target as HTMLElement).closest<HTMLElement>(
      ".react-flow__node",
    )?.dataset.id;
    if (id && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      onPick(id);
    }
  };
  return (
    <div className="pipe-canvas" onKeyDown={onKey}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        nodeTypes={NODE_TYPES}
        onNodeClick={(_, node) => onPick(node.id)}
        fitView
        fitViewOptions={{ padding: 0.08 }}
        minZoom={0.3}
        maxZoom={1.6}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        aria-label={label}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1.2} />
        <CanvasControls />
        <Follow point={focus} />
      </ReactFlow>
    </div>
  );
}

/** The four colour keys and the interaction hint shown above a canvas. */
export function CanvasLegend({ failed = false }: { failed?: boolean }) {
  return (
    <div className="pipe-legend-row">
      <div className="pipeline-legend" aria-hidden="true">
        <span>
          <i className="done" />
          Completed
        </span>
        <span>
          <i className="active" />
          Running
        </span>
        <span>
          <i className="pending" />
          Waiting
        </span>
        {failed && (
          <span>
            <i className="failed" />
            Failed
          </span>
        )}
        <span>
          <i className="skipped" />
          Not taken
        </span>
      </div>
      <span className="pipe-hint">
        Drag to pan · Scroll to zoom · Select a state
      </span>
    </div>
  );
}
