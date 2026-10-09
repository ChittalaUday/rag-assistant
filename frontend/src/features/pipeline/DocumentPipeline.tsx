import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { Node } from "@xyflow/react";
import { Search, X } from "lucide-react";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/animate-ui/components/radix/dialog";
import { Button } from "@/components/animate-ui/components/buttons/button";
import { api } from "@/api/client";
import type { Document, DocumentChunks } from "@/api/types";
import {
  buildEdges,
  CanvasLegend,
  StateCanvas,
  type CardData,
  type Status,
  type Transition,
} from "./canvas";

type DocState =
  | "checking"
  | "dedup"
  | "queued"
  | "parsing"
  | "situating"
  | "embedding"
  | "publishing"
  | "ready"
  | "failed";

/** The document state machine (docs/state-machines.md): what each state does, where it lives in
 *  the code, and its hand-placed spot on the canvas (a loose left-to-right drift, the failure below). */
const STATES: Record<
  DocState,
  {
    label: string;
    kind: string;
    x: number;
    y: number;
    does: string;
    code: string;
  }
> = {
  checking: {
    label: "Checking the upload",
    kind: "INTAKE",
    x: 0,
    y: 140,
    does: "The upload is checked: at most 20 MB, not empty, a supported type (TXT, Markdown, CSV, PDF, DOCX), room in the processing queue, and a replaced document not still processing.",
    code: "ingestion/intake.py · upload",
  },
  dedup: {
    label: "Deduplicating",
    kind: "HASH",
    x: 270,
    y: 35,
    does: "The content's SHA-256 is compared with every stored document. Identical content returns the existing document; new content is stored and queued. A replacement is staged so the old version stays searchable until the new one is published.",
    code: "ingestion/intake.py · upload",
  },
  queued: {
    label: "Queued",
    kind: "QUEUE",
    x: 525,
    y: 170,
    does: "Waiting for the single background worker, one document at a time. Jobs interrupted by a restart, and documents re-indexed after a pipeline or embedding-model change, wait here too.",
    code: "pipeline/indexing.py · worker",
  },
  parsing: {
    label: "Parsing",
    kind: "PARSE",
    x: 805,
    y: 60,
    does: "The text is read (PDF and DOCX through Docling) and normalised, cut into passages at headings and sentence ends, and any passage longer than the embedding model's window is split. No readable text, or more than 600 passages, fails the job.",
    code: "ingestion/parser.py, ingestion/chunker.py, pipeline/indexing.py · build",
  },
  situating: {
    label: "Situating",
    kind: "SUMMARISE",
    x: 640,
    y: 335,
    does: "The chat model writes one or two sentences per passage placing it in its document; they are searched and embedded with the passage. A passage whose summary fails keeps its plain location and the document gets a warning.",
    code: "ingestion/situate.py",
  },
  embedding: {
    label: "Embedding",
    kind: "EMBED",
    x: 965,
    y: 300,
    does: "Each passage, with its location and summary, is turned into a vector by the embedding model, in batches. Vectors of the wrong size or with invalid numbers fail the job.",
    code: "pipeline/indexing.py · build",
  },
  publishing: {
    label: "Publishing",
    kind: "PUBLISH",
    x: 1235,
    y: 155,
    does: "In one transaction: the parsed text is stored, the passages replace any older ones, the spelling vocabulary is rebuilt, and the document is marked ready.",
    code: "pipeline/indexing.py · publish",
  },
  ready: {
    label: "Ready",
    kind: "SEARCHABLE",
    x: 1290,
    y: 430,
    does: "The passages are searchable and can be cited. The document returns to the queue when it is replaced or re-indexed (an older pipeline version or a new embedding model).",
    code: "store/documents.py · mark_ready",
  },
  failed: {
    label: "Failed",
    kind: "ERROR",
    x: 825,
    y: 565,
    does: "Processing stopped and the error is kept on the document. Uploading the file again retries it; a job that runs past 30 minutes also ends here.",
    code: "pipeline/indexing.py · process, worker",
  },
};
const IDS = Object.keys(STATES) as DocState[];
const PROCESSING: DocState[] = [
  "parsing",
  "situating",
  "embedding",
  "publishing",
];
const position = (id: DocState) => ({ x: STATES[id].x, y: STATES[id].y });
const TRANSITIONS: Transition<DocState>[] = [
  { from: "checking", to: "dedup", label: "Accepted" },
  { from: "dedup", to: "queued", label: "New content" },
  { from: "queued", to: "parsing", label: "Worker" },
  {
    from: "parsing",
    to: "situating",
    label: "Summaries on",
  },
  {
    from: "parsing",
    to: "embedding",
    label: "Summaries off",
  },
  { from: "situating", to: "embedding" },
  {
    from: "embedding",
    to: "publishing",
    label: "Vectors valid",
  },
  { from: "publishing", to: "ready" },
  { from: "situating", to: "failed", label: "Error" },
  { from: "embedding", to: "failed" },
];
const STATUS_NAME: Record<Document["status"], string> = {
  queued: "Queued",
  processing: "Processing",
  ready: "Ready",
  failed: "Failed",
};
const suffix = (name: string) => name.split(".").pop()?.toUpperCase() ?? "";

/** A document's processing as a state machine, with its passages: opens in a large modal. Each
 *  state shows what it produced; select one for details. Built only from what the API returned. */
export function DocumentPipeline({
  document,
  onClose,
  onOpenDocument,
}: {
  document: Document | null;
  onClose: () => void;
  onOpenDocument: (doc: Document) => void;
}) {
  const [data, setData] = useState<DocumentChunks | null>(null);
  const [error, setError] = useState("");
  const [picked, setPicked] = useState<DocState | null>(null);
  const [filter, setFilter] = useState("");
  const id = document?.id ?? null;
  const settled = document
    ? `${document.status}:${document.chunk_count}:${document.pipeline_version}`
    : "";

  // passages: on opening, and again whenever processing settles into a new state
  useEffect(() => {
    if (!id) {
      setData(null);
      return;
    }
    let active = true;
    setError("");
    void api
      .documentChunks(id)
      .then((result) => active && setData(result))
      .catch(
        (e: unknown) =>
          active &&
          setError(
            e instanceof Error ? e.message : "Could not load the passages",
          ),
      );
    return () => {
      active = false;
    };
  }, [id, settled]);
  useEffect(() => {
    setPicked(null);
    setFilter("");
  }, [id]);

  const chunks = useMemo(
    () => (data?.document.id === id ? data.chunks : []),
    [data, id],
  );
  const summarised = chunks.filter((c) => c.summary).length;
  const textChars = data?.document.id === id ? data.text_chars : 0;
  const stage = document?.processing_stage ?? "";

  const statusOf = (s: DocState): Status => {
    if (!document) return "pending";
    if (s === "checking" || s === "dedup") return "done";
    if (document.status === "failed")
      return s === "failed" ? "failed" : s === "queued" ? "done" : "skipped";
    if (s === "failed")
      return document.status === "ready" ? "skipped" : "pending";
    if (document.status === "queued")
      return s === "queued" ? "active" : "pending";
    if (document.status === "processing") {
      if (s === "queued") return "done";
      if (s === "ready") return "pending";
      const at = PROCESSING.indexOf(stage as DocState),
        me = PROCESSING.indexOf(s);
      return me < at ? "done" : me === at ? "active" : "pending";
    }
    if (s === "situating" && chunks.length > 0 && summarised === 0)
      return "skipped";
    return "done";
  };
  const status = Object.fromEntries(IDS.map((s) => [s, statusOf(s)])) as Record<
    DocState,
    Status
  >;
  const progress =
    document && document.total_chunks
      ? `${document.processed_chunks} of ${document.total_chunks} passages`
      : "running";
  const n = chunks.length || document?.chunk_count || 0;

  const subOf = (s: DocState): string => {
    const st = status[s];
    if (st === "pending") return "waiting";
    if (st === "skipped")
      return s === "situating" ? "summaries off" : "not completed";
    if (st === "active")
      return s === "queued" ? "waiting for the worker" : progress;
    if (!document) return "";
    switch (s) {
      case "checking":
        return `accepted · ${suffix(document.name)}`;
      case "dedup":
        return `new · ${document.content_hash.slice(0, 10)}…`;
      case "queued":
        return `uploaded ${new Date(document.created_at).toLocaleString()}`;
      case "parsing":
        return textChars
          ? `${textChars.toLocaleString("en")} characters · ${n} passages`
          : `${n} passages`;
      case "situating":
        return `${summarised} of ${chunks.length} summarised`;
      case "embedding":
        return `${n} vectors · ${chunks[0]?.embedding_model ?? "embedding model"}`;
      case "publishing":
        return `pipeline version ${document.pipeline_version ?? "–"}`;
      case "ready":
        return `${document.chunk_count} passages searchable`;
      case "failed":
        return (document.error ?? "error").slice(0, 60);
    }
  };
  const activeState = IDS.find((s) => status[s] === "active");
  const selected: DocState =
    picked ??
    activeState ??
    (document?.status === "failed" ? "failed" : "parsing");
  const cards: Node<CardData>[] = IDS.map((s) => ({
    id: s,
    type: "state",
    position: position(s),
    data: {
      kind: STATES[s].kind,
      label: STATES[s].label,
      status: status[s],
      sub: subOf(s),
      share: null,
      note:
        s === "ready" && document?.warnings.length
          ? `${document.warnings.length} warning${document.warnings.length > 1 ? "s" : ""}`
          : undefined,
      selected: selected === s,
    },
    ariaLabel: `${STATES[s].label}: ${subOf(s)}`,
  }));
  // with summaries the path goes through situating; the direct parsing-to-embedding edge did not run
  const edges = buildEdges(TRANSITIONS, status, position).map((e) =>
    e.id === "parsing-embedding" &&
    (status.situating === "done" || status.situating === "active")
      ? { ...e, className: "pipe-edge", animated: false }
      : e,
  );

  const needle = filter.trim().toLowerCase();
  const shown = needle
    ? chunks.filter((c) =>
        `${c.heading ?? ""} ${c.text} ${c.summary}`
          .toLowerCase()
          .includes(needle),
      )
    : chunks;

  return (
    <Dialog
      open={Boolean(document)}
      onOpenChange={(open) => !open && onClose()}
    >
      <DialogContent className="doc-pipeline-modal" showCloseButton={false}>
        <div className="doc-pipeline-head">
          <div>
            <div className="eyebrow">HOW THIS DOCUMENT WAS PROCESSED</div>
            <DialogTitle>{document?.name ?? "Document"}</DialogTitle>
            <DialogDescription className="muted">
              From upload to searchable passages. Select a state for what it
              did; the passages it produced are listed below.
            </DialogDescription>
          </div>
          <DialogClose asChild>
            <Button variant="ghost" size="icon-sm" aria-label="Close">
              <X size={16} />
            </Button>
          </DialogClose>
        </div>

        {document && (
          <>
            <div className="pipe-tiles">
              <div className="pipe-tile">
                <strong>{STATUS_NAME[document.status]}</strong>
                <span>Status</span>
              </div>
              <div className="pipe-tile">
                <strong>{document.chunk_count}</strong>
                <span>Passages</span>
              </div>
              <div className="pipe-tile">
                <strong>
                  {textChars ? textChars.toLocaleString("en") : "–"}
                </strong>
                <span>Characters parsed</span>
              </div>
              <div className="pipe-tile">
                <strong>
                  {chunks.length ? `${summarised}/${chunks.length}` : "–"}
                </strong>
                <span>Passages summarised</span>
              </div>
            </div>
            <CanvasLegend failed />
            <StateCanvas
              key={document.id}
              cards={cards}
              edges={edges}
              focus={activeState ? position(activeState) : undefined}
              onPick={(s) => s in STATES && setPicked(s as DocState)}
              label="Document state machine"
            />
            <StateDetails
              id={selected}
              status={status[selected]}
              document={document}
              rows={detailRows(selected, document, data, summarised)}
            />

            <section className="doc-chunks" aria-label="Passages">
              <div className="doc-chunks-head">
                <h3>
                  Passages{" "}
                  <span>
                    {needle
                      ? `${shown.length} of ${chunks.length}`
                      : chunks.length}
                  </span>
                </h3>
                <label className="doc-chunk-filter">
                  <Search size={13} />
                  <input
                    id="doc-chunk-filter"
                    type="search"
                    placeholder="Filter by heading or text…"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                    aria-label="Filter passages"
                  />
                </label>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onOpenDocument(document)}
                >
                  Open full document
                </Button>
              </div>
              {error && (
                <p role="alert" className="reader-error">
                  {error}
                </p>
              )}
              {!error && chunks.length === 0 && (
                <p className="trace-empty">
                  {document.status === "ready"
                    ? "Loading the passages…"
                    : "Passages appear here once the document is published."}
                </p>
              )}
              <ol className="doc-chunk-list">
                {shown.map((c) => (
                  <ChunkCard key={c.id} chunk={c} />
                ))}
              </ol>
            </section>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function ChunkCard({ chunk }: { chunk: DocumentChunks["chunks"][number] }) {
  const [open, setOpen] = useState(false);
  const long = chunk.text.length > 420;
  return (
    <li className="doc-chunk">
      <div className="doc-chunk-head">
        <span className="doc-chunk-n">#{chunk.position + 1}</span>
        <strong dir="auto">{chunk.heading ?? "No heading"}</strong>
        <span className="doc-chunk-meta">
          {chunk.char_start.toLocaleString("en")}–
          {chunk.char_end.toLocaleString("en")} ·{" "}
          {chunk.text.length.toLocaleString("en")} chars
          {chunk.page ? ` · page ${chunk.page}` : ""}
        </span>
      </div>
      <div className="doc-chunk-location" dir="auto">
        {chunk.location}
      </div>
      {chunk.summary && (
        <p className="doc-chunk-summary" dir="auto">
          {chunk.summary}
        </p>
      )}
      <p
        className={`doc-chunk-text ${open || !long ? "" : "clamped"}`}
        dir="auto"
      >
        {chunk.text}
      </p>
      {long && (
        <button
          type="button"
          className="doc-chunk-more"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
          {open ? "Show less" : "Show the whole passage"}
        </button>
      )}
    </li>
  );
}

function detailRows(
  s: DocState,
  d: Document,
  data: DocumentChunks | null,
  summarised: number,
): [string, ReactNode][] {
  const chunks = data?.document.id === d.id ? data.chunks : [];
  const rows: [string, ReactNode][] = [];
  const lengths = chunks.map((c) => c.text.length);
  switch (s) {
    case "checking":
      rows.push(["file", d.name], ["type", suffix(d.name)]);
      if (d.labels?.length) rows.push(["labels", d.labels.join(", ")]);
      break;
    case "dedup":
      rows.push(["content hash", <code key="h">{d.content_hash}</code>]);
      break;
    case "queued":
      rows.push(["uploaded", new Date(d.created_at).toLocaleString()]);
      if (d.status === "queued" || d.status === "processing")
        rows.push(["now", d.processing_message ?? d.status]);
      break;
    case "parsing":
      if (data?.text_chars)
        rows.push([
          "parsed text",
          `${data.text_chars.toLocaleString("en")} characters`,
        ]);
      if (chunks.length) {
        rows.push([
          "passages",
          `${chunks.length}, ${Math.min(...lengths)}–${Math.max(...lengths)} characters each (average ${Math.round(lengths.reduce((a, b) => a + b, 0) / lengths.length)})`,
        ]);
        rows.push([
          "headings",
          `${new Set(chunks.map((c) => c.heading).filter(Boolean)).size} distinct`,
        ]);
      }
      d.warnings
        .filter((w) => w.includes("split"))
        .forEach((w) => rows.push(["warning", w]));
      break;
    case "situating":
      if (chunks.length)
        rows.push(["summaries", `${summarised} of ${chunks.length} passages`]);
      d.warnings
        .filter((w) => w.includes("summarised"))
        .forEach((w) => rows.push(["warning", w]));
      if (chunks.length && !summarised)
        rows.push([
          "why skipped",
          "contextual summaries were off when this document was indexed",
        ]);
      break;
    case "embedding":
      if (chunks.length)
        rows.push(
          ["vectors", `${chunks.length}`],
          [
            "model",
            [...new Set(chunks.map((c) => c.embedding_model))].join(", "),
          ],
        );
      break;
    case "publishing":
      rows.push(["pipeline version", `${d.pipeline_version ?? "–"}`]);
      break;
    case "ready":
      rows.push(["passages", `${d.chunk_count} searchable`]);
      d.warnings.forEach((w) => rows.push(["warning", w]));
      break;
    case "failed":
      if (d.error) rows.push(["error", d.error]);
      if (d.chunk_count)
        rows.push([
          "still searchable",
          `${d.chunk_count} passages from the last successful version`,
        ]);
      break;
  }
  if (
    (d.status === "processing" || d.status === "queued") &&
    d.processing_stage === s &&
    d.total_chunks
  )
    rows.push([
      "progress",
      `${d.processed_chunks} of ${d.total_chunks} passages`,
    ]);
  return rows;
}

function StateDetails({
  id,
  status,
  rows,
}: {
  id: DocState;
  status: Status;
  document: Document;
  rows: [string, ReactNode][];
}) {
  const state = STATES[id];
  const chip = {
    done: "done",
    active: "running",
    pending: "waiting",
    skipped: "not taken",
    failed: "failed",
  }[status];
  return (
    <div
      className="pipe-details"
      role="region"
      aria-label={`${state.label} details`}
      aria-live="polite"
    >
      <div className="pipe-details-head">
        <h3>{state.label}</h3>
        <span className={`pipe-chip ${status}`}>{chip}</span>
      </div>
      <p className="pipe-does">{state.does}</p>
      {rows.length > 0 && (
        <dl className="pipeline-out">
          {rows.map(([k, v], i) => (
            <div key={`${k}-${i}`}>
              <dt>{k}</dt>
              <dd dir="auto">{v}</dd>
            </div>
          ))}
        </dl>
      )}
      <dl className="pipeline-out pipe-meta">
        <div>
          <dt>code</dt>
          <dd>
            <code>backend/knowledge_engine/{state.code}</code>
          </dd>
        </div>
      </dl>
    </div>
  );
}
