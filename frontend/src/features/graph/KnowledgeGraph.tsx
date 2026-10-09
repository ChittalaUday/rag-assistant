import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import type Sigma from "sigma";
import {
  ArrowRight,
  FileText,
  Maximize,
  Minus,
  Network,
  Plus,
  ScanText,
} from "lucide-react";
import type { GraphRelation, PreviewGraph } from "@/api/types";
import { Button } from "@/components/animate-ui/components/buttons/button";

// sigma draws on a canvas, so its colours cannot come from CSS variables
const ACCENT = "#3b6554";
const NODE = "#8caaa9";
const EDGE = "#c3cec0";
const DIM = "#e8ece6";

// the API names scripts as Unicode does ("TELUGU"); JS regexes want "Telugu"
const scriptName = (script: string) =>
  script.charAt(0) + script.slice(1).toLowerCase();

/** Which of the scripts most letters of the text are written in ("" when none). */
function scriptOf(text: string, scripts: string[]): string {
  let best = "";
  let most = 0;
  for (const script of scripts) {
    let count = 0;
    try {
      count =
        text.match(new RegExp(`\\p{Script=${scriptName(script)}}`, "gu"))
          ?.length ?? 0;
    } catch {
      continue; // a script name JS does not know
    }
    if (count > most) [best, most] = [script, count];
  }
  return best;
}

/** The part of a graph stated in documents of one script. */
function inScript(graph: PreviewGraph | null, script: string) {
  if (!graph || !script) return graph;
  const relations = graph.relations.filter((r) => (r.script ?? "") === script);
  const linked = new Set(relations.flatMap((r) => [r.source, r.target]));
  return {
    entities: graph.entities.filter((e) => linked.has(e.id) || e.typed),
    relations,
  };
}

const relationKey = (r: GraphRelation) => `${r.source}|${r.label}|${r.target}`;
// "arun balla" selects "deputy ceo arun balla": a recognised name selects every name containing it
const contains = (name: string, part: string) =>
  ` ${name} `.includes(` ${part} `);

/** Every name and relation in the documents, drawn with sigma (zoom, pan, drag, hover, click a
 *  name to focus it, click a relation to open its passage). While a question is typed, the names
 *  recognised in it are selected in the graph and the list below shows their live relations. */
export function KnowledgeGraph({
  full: everything,
  live: typedGraph,
  text,
  onSource,
}: {
  full: PreviewGraph | null;
  live: PreviewGraph | null;
  text: string | null;
  onSource: (chunkId: string) => void;
}) {
  // one language at a time: the one chosen, else the one the question is typed in, else the
  // language most relations are in
  const languages = useMemo(() => {
    const counts = new Map<string, number>();
    for (const r of everything?.relations ?? [])
      if (r.script) counts.set(r.script, (counts.get(r.script) ?? 0) + 1);
    return [...counts].sort((a, b) => b[1] - a[1]);
  }, [everything]);
  const [chosen, setChosen] = useState<string | null>(null);
  const scripts = languages.map(([script]) => script);
  const typedIn = text ? scriptOf(text, scripts) : "";
  const language =
    (chosen && scripts.includes(chosen) ? chosen : typedIn) || scripts[0] || "";
  const full = useMemo(
    () => inScript(everything, language),
    [everything, language],
  );
  const live = useMemo(
    () => inScript(typedGraph, language),
    [typedGraph, language],
  );
  const reduceMotion = useReducedMotion();
  const container = useRef<HTMLDivElement>(null);
  const renderer = useRef<Sigma | null>(null);
  const [data] = useState(() => new Graph({ type: "directed", multi: true }));
  const openSource = useRef(onSource);
  const [ready, setReady] = useState(false);
  const [renderError, setRenderError] = useState(false);
  const [focus, setFocus] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);

  useEffect(() => {
    openSource.current = onSource;
  }, [onSource]);

  // one renderer for the life of the panel
  useEffect(() => {
    let cancelled = false;
    let instance: Sigma | null = null;
    void import("sigma")
      .then(({ default: SigmaConstructor }) => {
        if (cancelled || !container.current) return;
        instance = new SigmaConstructor(data, container.current, {
          renderEdgeLabels: true,
          enableEdgeEvents: true,
          defaultEdgeType: "arrow",
          labelFont: "Helvetica Neue, Arial, sans-serif",
          labelSize: 11,
          labelColor: { color: "#34493e" },
          labelRenderedSizeThreshold: 5,
          edgeLabelSize: 9,
          edgeLabelColor: { color: "#6c7a70" },
          minCameraRatio: 0.05,
          maxCameraRatio: 3,
        });
        const sigma = instance;
        let dragging: string | null = null;
        sigma.on("downNode", ({ node }) => {
          dragging = node;
          if (!sigma.getCustomBBox()) sigma.setCustomBBox(sigma.getBBox());
        });
        sigma.on("moveBody", ({ event }) => {
          if (!dragging) return;
          const at = sigma.viewportToGraph(event);
          data.mergeNodeAttributes(dragging, { x: at.x, y: at.y });
          event.preventSigmaDefault();
          event.original.preventDefault();
          event.original.stopPropagation();
        });
        const drop = () => {
          dragging = null;
        };
        sigma.on("upNode", drop);
        sigma.on("upStage", drop);
        sigma.on("enterNode", ({ node }) => setHovered(node));
        sigma.on("leaveNode", () => setHovered(null));
        sigma.on("clickNode", ({ node }) =>
          setFocus((current) => (current === node ? null : node)),
        );
        sigma.on("clickStage", () => setFocus(null));
        sigma.on("clickEdge", ({ edge }) =>
          openSource.current(data.getEdgeAttribute(edge, "chunk") as string),
        );
        renderer.current = sigma;
        setReady(true);
      })
      .catch(() => {
        if (!cancelled) setRenderError(true);
      });
    return () => {
      cancelled = true;
      instance?.kill();
      renderer.current = null;
    };
  }, [data]);

  // (re)build and lay out the full graph when the documents change
  useEffect(() => {
    data.clear();
    if (!full) return;
    const degree = new Map<string, number>();
    for (const r of full.relations) {
      degree.set(r.source, (degree.get(r.source) ?? 0) + 1);
      degree.set(r.target, (degree.get(r.target) ?? 0) + 1);
    }
    full.entities.forEach((e, i) => {
      const angle = i * 2.399963; // golden-angle spiral: a spread-out start for the layout
      data.addNode(e.id, {
        label: e.name,
        x: Math.cos(angle) * Math.sqrt(i + 1),
        y: Math.sin(angle) * Math.sqrt(i + 1),
        size: 4 + Math.min(degree.get(e.id) ?? 0, 6),
        color: NODE,
      });
    });
    for (const r of full.relations)
      if (data.hasNode(r.source) && data.hasNode(r.target))
        data.addDirectedEdgeWithKey(relationKey(r), r.source, r.target, {
          label: r.label,
          chunk: r.chunk_id,
          size: 1.2,
          color: EDGE,
        });
    if (data.order > 1)
      forceAtlas2.assign(data, {
        iterations: 120,
        settings: {
          ...forceAtlas2.inferSettings(data),
          gravity: 1,
          scalingRatio: 6,
        },
      });
    renderer.current?.setCustomBBox(null);
  }, [full, data, ready]);

  // names recognised in the typed text, selected in the full graph
  const recognised = useMemo(
    () => (live?.entities ?? []).filter((e) => e.typed),
    [live],
  );
  const selected = useMemo(
    () =>
      new Set(
        (full?.entities ?? [])
          .filter((n) =>
            recognised.some((e) => e.found && contains(n.id, e.id)),
          )
          .map((n) => n.id),
      ),
    [full, recognised],
  );

  // hover or a clicked name lights it and its neighbours; otherwise the selection is lit
  const lit = hovered ?? focus;
  useEffect(() => {
    const sigma = renderer.current;
    if (!sigma) return;
    const centre = lit && data.hasNode(lit) ? new Set([lit]) : selected;
    const near = new Set(centre);
    for (const id of centre)
      if (data.hasNode(id)) data.neighbors(id).forEach((n) => near.add(n));
    const active = near.size > 0;
    sigma.setSetting("nodeReducer", (node, attrs) =>
      !active
        ? attrs
        : near.has(node)
          ? {
              ...attrs,
              color: centre.has(node) ? ACCENT : NODE,
              size: centre.has(node) ? attrs.size + 3 : attrs.size,
              highlighted: centre.has(node),
              forceLabel: true,
              zIndex: 1,
            }
          : { ...attrs, color: DIM, label: "" },
    );
    sigma.setSetting("edgeReducer", (edge, attrs) =>
      !active
        ? attrs
        : data.extremities(edge).some((end) => centre.has(end))
          ? { ...attrs, color: ACCENT, size: 2 }
          : { ...attrs, color: DIM, label: "" },
    );
  }, [lit, selected, ready, data, full]);

  // bring the selection into view as it changes; back to the whole graph when it empties
  const selectionKey = [...selected].join("|");
  useEffect(() => {
    const sigma = renderer.current;
    if (!sigma) return;
    const options = { duration: reduceMotion ? 0 : 300 };
    const ids = selectionKey ? selectionKey.split("|") : [];
    // the camera moves in framed-graph coordinates, not the layout's
    const points = ids
      .filter((id) => data.hasNode(id))
      .map((id) =>
        sigma.viewportToFramedGraph(
          sigma.graphToViewport(
            data.getNodeAttributes(id) as { x: number; y: number },
          ),
        ),
      );
    if (!points.length) {
      void sigma.getCamera().animatedReset(options);
      return;
    }
    const x = points.reduce((sum, p) => sum + p.x, 0) / points.length;
    const y = points.reduce((sum, p) => sum + p.y, 0) / points.length;
    // zoom in on one name; zoom out just enough to frame several (framed graph spans 0..1)
    const spread = Math.max(...points.map((p) => Math.hypot(p.x - x, p.y - y)));
    void sigma
      .getCamera()
      .animate(
        { x, y, ratio: Math.min(Math.max(spread * 2.4, 0.45), 1) },
        options,
      );
  }, [selectionKey, ready, reduceMotion, full, data]);

  const camera = (move: "in" | "out" | "fit") => {
    const sigma = renderer.current;
    if (!sigma) return;
    const options = { duration: reduceMotion ? 0 : 250 };
    if (move === "in") void sigma.getCamera().animatedZoom(options);
    else if (move === "out") void sigma.getCamera().animatedUnzoom(options);
    else {
      setFocus(null);
      void sigma.getCamera().animatedReset(options);
    }
  };

  const names = new Map(
    [...(full?.entities ?? []), ...(live?.entities ?? [])].map((e) => [
      e.id,
      e.name,
    ]),
  );
  const relations = focus
    ? (full?.relations ?? []).filter(
        (r) => r.source === focus || r.target === focus,
      )
    : live?.relations.length
      ? live.relations
      : (full?.relations ?? []);
  const scope = focus
    ? `of ${names.get(focus)}`
    : live?.relations.length
      ? "for your question"
      : "in all documents";
  const empty = !full?.entities.length;

  return (
    <section className="graph-panel" aria-label="Knowledge graph">
      {languages.length > 1 && (
        <label className="graph-language">
          Language
          <select
            value={chosen ?? ""}
            onChange={(e) => setChosen(e.target.value || null)}
          >
            <option value="">
              Follow the question ({scriptName(typedIn || scripts[0])})
            </option>
            {languages.map(([script, count]) => (
              <option key={script} value={script}>
                {scriptName(script)} · {count} relations
              </option>
            ))}
          </select>
        </label>
      )}
      <div className="graph-recognised" aria-live="polite">
        <ScanText size={13} />
        {recognised.length ? (
          recognised.map((e) => (
            <span
              key={e.id}
              className={`chip graph-entity ${e.found ? "" : "unknown"}`}
              title={e.found ? "Selected in the graph" : "In no document"}
            >
              {e.name}
              {!e.found && <small> · not in documents</small>}
            </span>
          ))
        ) : (
          <span className="graph-hint" dir="auto">
            {text
              ? `No names recognised in “${text}”`
              : "Type a question: the names it mentions are selected in the graph"}
          </span>
        )}
      </div>

      <div className="graph-area">
        <div
          ref={container}
          className="graph-canvas"
          role="img"
          aria-label={`${full?.entities.length ?? 0} names, ${full?.relations.length ?? 0} relations`}
        />
        {ready && !empty && (
          <div className="graph-zoom">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Zoom in"
              onClick={() => camera("in")}
            >
              <Plus size={14} />
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Zoom out"
              onClick={() => camera("out")}
            >
              <Minus size={14} />
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Show the whole graph"
              onClick={() => camera("fit")}
            >
              <Maximize size={14} />
            </Button>
          </div>
        )}
        {(renderError || empty) && (
          <p className="graph-empty">
            <Network size={14} />
            {renderError
              ? "The graph view needs WebGL, which this browser does not offer. Every relation is listed below."
              : "Names and the sentences that relate them appear here once documents are processed."}
          </p>
        )}
      </div>

      <div className="graph-relations">
        <div className="graph-relations-title">
          <ArrowRight size={13} /> Relations
          <span>
            {relations.length} {scope}
          </span>
          {focus && (
            <button
              type="button"
              className="graph-clear"
              onClick={() => setFocus(null)}
            >
              show all
            </button>
          )}
        </div>
        <ul>
          <AnimatePresence initial={false}>
            {relations.map((r) => (
              <motion.li
                key={relationKey(r)}
                layout={!reduceMotion}
                initial={reduceMotion ? false : { opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduceMotion ? undefined : { opacity: 0 }}
                onMouseEnter={() => setHovered(r.source)}
                onMouseLeave={() => setHovered(null)}
              >
                <button type="button" onClick={() => onSource(r.chunk_id)}>
                  <span dir="auto">
                    {names.get(r.source)} <strong>{r.label}</strong>{" "}
                    {names.get(r.target)}
                  </span>
                  <small dir="auto">
                    <FileText size={11} /> {r.document} · “{r.quote}”
                  </small>
                </button>
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      </div>
    </section>
  );
}
