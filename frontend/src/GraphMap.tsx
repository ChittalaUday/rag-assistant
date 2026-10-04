import { useEffect, useRef, useState } from "react";
import Graph from "graphology";
import type Sigma from "sigma";
import forceAtlas2 from "graphology-layout-forceatlas2";
import { Maximize, Minus, Plus, Search, Network } from "lucide-react";
import type { Citation, KnowledgeGraph } from "./types";
export function Relationships({
  graph,
  onEvidence,
}: {
  graph: KnowledgeGraph;
  onEvidence: (citation: Citation) => void;
}) {
  const labels = new Map(graph.nodes.map((n) => [n.id, n.label]));
  return (
    <div className="relationships">
      <h3>
        Source-backed relationships <span>{graph.edges.length}</span>
      </h3>
      {graph.edges.length === 0 ? (
        <p className="muted">Relationships appear after document processing.</p>
      ) : (
        [...graph.edges]
          .sort(
            (a, b) =>
              Number(graph.highlighted_edge_ids.includes(b.id)) -
              Number(graph.highlighted_edge_ids.includes(a.id)),
          )
          .map((edge) => (
            <div key={edge.id}>
              <button
                className={`relationship ${graph.highlighted_edge_ids.includes(edge.id) ? "focused" : ""}`}
                onClick={() => {
                  if (edge.evidence[0]) onEvidence(edge.evidence[0]);
                }}
              >
                <span>
                  {labels.get(edge.source)} <strong>{edge.label}</strong>{" "}
                  {labels.get(edge.target)}
                </span>
                <small>
                  {edge.evidence.length} supporting passage
                  {edge.evidence.length !== 1 ? "s" : ""}
                </small>
              </button>
              {edge.evidence.length > 1 && (
                <div className="more-evidence">
                  {edge.evidence.slice(1).map((citation, index) => (
                    <button
                      key={`${citation.chunk_id}-${index}`}
                      onClick={() => onEvidence(citation)}
                    >
                      Passage {index + 2} · {citation.document_name}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))
      )}
    </div>
  );
}
export function GraphMap({
  graph,
  onEvidence,
}: {
  graph: KnowledgeGraph;
  onEvidence: (citation: Citation) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const sigma = useRef<Sigma | null>(null);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [renderError, setRenderError] = useState(false);
  const [SigmaConstructor, setSigmaConstructor] = useState<
    typeof import("sigma").default | null
  >(null);
  useEffect(() => {
    let cancelled = false;
    void import("sigma")
      .then((module) => {
        if (!cancelled) setSigmaConstructor(() => module.default);
      })
      .catch(() => {
        if (!cancelled) setRenderError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  useEffect(() => {
    if (!container.current || !graph.nodes.length || !SigmaConstructor) return;
    const data = new Graph({ multi: true });
    const degree = new Map<string, number>();
    graph.edges.forEach((e) => {
      degree.set(e.source, (degree.get(e.source) || 0) + 1);
      degree.set(e.target, (degree.get(e.target) || 0) + 1);
    });
    graph.nodes.forEach((node, index) => {
      const angle = index * 2.399963;
      const radius = Math.sqrt(index + 1);
      data.addNode(node.id, {
        label: node.label,
        x: Math.cos(angle) * radius,
        y: Math.sin(angle) * radius,
        size: 6 + Math.min(degree.get(node.id) || 0, 6),
        color: graph.highlighted_node_ids.includes(node.id)
          ? "#247b8a"
          : "#8caaa9",
        type: "circle",
      });
    });
    graph.edges.forEach((edge) => {
      if (data.hasNode(edge.source) && data.hasNode(edge.target))
        data.addEdgeWithKey(edge.id, edge.source, edge.target, {
          label: edge.label,
          size: graph.highlighted_edge_ids.includes(edge.id) ? 3 : 1.2,
          color: graph.highlighted_edge_ids.includes(edge.id)
            ? "#247b8a"
            : "#d0d9d7",
        });
    });
    if (data.order > 1 && data.order < 800)
      forceAtlas2.assign(data, {
        iterations: 80,
        settings: {
          ...forceAtlas2.inferSettings(data),
          gravity: 1,
          scalingRatio: 5,
        },
      });
    let renderer: Sigma;
    try {
      renderer = new SigmaConstructor(data, container.current, {
        renderEdgeLabels: true,
        enableEdgeEvents: true,
        labelFont: "Helvetica Neue",
        labelSize: 12,
        labelColor: { color: "#354341" },
        edgeLabelSize: 11,
        edgeLabelColor: { color: "#64716e" },
        defaultEdgeType: "line",
      });
      sigma.current = renderer;
      renderer.on("clickEdge", ({ edge }) => {
        const citation = graph.edges.find((e) => e.id === edge)?.evidence[0];
        if (citation) onEvidence(citation);
      });
      renderer.on("clickNode", ({ node }) => setSelected(node));
      renderer.on("clickStage", () => setSelected(null));
    } catch {
      queueMicrotask(() => setRenderError(true));
      return;
    }
    return () => {
      renderer.kill();
      sigma.current = null;
    };
  }, [graph, onEvidence, SigmaConstructor]);
  useEffect(() => {
    const renderer = sigma.current;
    if (!renderer) return;
    const query = search.toLocaleLowerCase();
    const matching = new Set(
      graph.nodes
        .filter((n) => n.label.toLocaleLowerCase().includes(query))
        .map((n) => n.id),
    );
    const neighbors = new Set<string>();
    if (selected) {
      neighbors.add(selected);
      graph.edges
        .filter((e) => e.source === selected || e.target === selected)
        .forEach((e) => {
          neighbors.add(e.source);
          neighbors.add(e.target);
        });
    }
    renderer.setSetting("nodeReducer", (node, attributes) => ({
      ...attributes,
      color:
        (selected && !neighbors.has(node)) || (query && !matching.has(node))
          ? "#e1e7e5"
          : attributes.color,
      highlighted: node === selected || Boolean(query && matching.has(node)),
      forceLabel: node === selected || Boolean(query && matching.has(node)),
    }));
    renderer.setSetting("edgeReducer", (edge, attributes) => ({
      ...attributes,
      color:
        graph.highlighted_edge_ids.length &&
        !graph.highlighted_edge_ids.includes(edge)
          ? "#e7ece7"
          : attributes.color,
    }));
    if (query && matching.size === 1) {
      const node = [...matching][0];
      const position = renderer.getNodeDisplayData(node);
      if (position)
        void renderer
          .getCamera()
          .animate(
            { x: position.x, y: position.y, ratio: 0.5 },
            { duration: 250 },
          );
    }
    renderer.refresh();
  }, [search, selected, graph, SigmaConstructor]);
  const selectedNode = graph.nodes.find((n) => n.id === selected);
  return (
    <section className="map-panel" aria-label="Knowledge map">
      <header className="panel-header">
        <div>
          <div className="eyebrow">DOCUMENT INTELLIGENCE</div>
          <h2>Knowledge map</h2>
        </div>
        <span className="count">
          {graph.nodes.length} entities · {graph.edges.length} relations
        </span>
      </header>
      <div className="map-tools">
        <label className="search">
          <Search size={15} />
          <input
            aria-label="Find an entity"
            placeholder="Find an entity…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <div className="zoom">
          <button
            aria-label="Zoom in"
            onClick={() =>
              void sigma.current?.getCamera().animatedZoom({ duration: 200 })
            }
          >
            <Plus size={16} />
          </button>
          <button
            aria-label="Zoom out"
            onClick={() =>
              void sigma.current?.getCamera().animatedUnzoom({ duration: 200 })
            }
          >
            <Minus size={16} />
          </button>
          <button
            aria-label="Fit graph"
            onClick={() => {
              setSearch("");
              setSelected(null);
              void sigma.current?.getCamera().animatedReset({ duration: 250 });
            }}
          >
            <Maximize size={16} />
          </button>
        </div>
      </div>
      <div className="map-area">
        <div ref={container} className="sigma-canvas" />
        {(!graph.nodes.length || renderError) && (
          <div className="map-empty">
            <Network size={38} strokeWidth={1} />
            <h3>
              {renderError
                ? "Use the relationship list below"
                : "Your company, connected."}
            </h3>
            <p>
              {renderError
                ? "The graph renderer is unavailable in this browser. Every relationship and source remains accessible."
                : "Upload documents to discover the people, projects, and decisions connected by their source passages."}
            </p>
          </div>
        )}
        <div className="map-legend">
          <i />{" "}
          {graph.highlighted_edge_ids.length
            ? "Teal connections support your latest answer"
            : "Every connection has document evidence"}
        </div>
        {selectedNode && (
          <div className="node-detail">
            <strong>{selectedNode.label}</strong>
            <span>
              {selectedNode.type} · {selectedNode.document_ids.length} documents
            </span>
            <button onClick={() => setSelected(null)}>Clear focus</button>
          </div>
        )}
      </div>
      <Relationships
        graph={
          selected
            ? {
                ...graph,
                edges: graph.edges.filter(
                  (e) => e.source === selected || e.target === selected,
                ),
              }
            : graph
        }
        onEvidence={onEvidence}
      />
    </section>
  );
}
