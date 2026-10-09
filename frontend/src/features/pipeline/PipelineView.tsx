import { useState, type ReactNode } from "react";
import type { Node } from "@xyflow/react";
import { Workflow } from "lucide-react";
import {
  buildEdges,
  CanvasLegend,
  secs,
  StateCanvas,
  subLine,
  type CardData,
  type Status,
  type Transition,
} from "./canvas";
import type { Message } from "@/api/types";

export type LiveStage = { stage: string; at: number };
export type Live = { stages: LiveStage[]; startedAt: number; now: number };

type Branch = "small_talk" | "documents";
type StateId =
  | "routing"
  | "replying"
  | "understanding"
  | "searching"
  | "reranking"
  | "evidence"
  | "generating"
  | "validating"
  | "verifying"
  | "noinfo"
  | "done";

/** The question state machine (docs/state-machines.md): what each state does, the backend stage
 *  events it covers, where it lives in the code, and its place in the diagram (column, row). */
const STATES: Record<
  StateId,
  {
    label: string;
    stages: string[];
    branch?: Branch;
    kind: string; // the label above the title on its card
    x: number; // hand-placed: a state machine, not a grid
    y: number;
    does: string;
    code: string;
  }
> = {
  routing: {
    label: "Routing",
    stages: ["routing"],
    kind: "ROUTE",
    x: 0,
    y: 197,
    does: "Embeds the message and compares it with example small-talk and question messages. Small talk goes left to a short reply; anything else is answered from the documents.",
    code: "generation/smalltalk.py · route_score",
  },
  replying: {
    label: "Small-talk reply",
    stages: ["replying"],
    branch: "small_talk",
    kind: "TALK",
    x: 212,
    y: 25,
    does: "The chat model writes one or two sentences. It sees no passages and is told to state no facts, so the reply has no citations.",
    code: "generation/smalltalk.py · small_talk_reply",
  },
  understanding: {
    label: "Understanding",
    stages: ["retrieving", "rewriting"],
    branch: "documents",
    kind: "UNDERSTAND",
    x: 246,
    y: 275,
    does: "A follow-up is rewritten into a standalone question using the conversation; spelling is corrected from the documents' own words; the intent (lookup, relationship, overview) is classified.",
    code: "retrieval/analyzer.py · analyze",
  },
  searching: {
    label: "Searching",
    stages: ["embedding_query", "searching"],
    branch: "documents",
    kind: "SEARCH",
    x: 502,
    y: 201,
    does: "The question is embedded and searched two ways, by meaning and by keywords; the two rankings are fused into one candidate list. Nothing found ends in no information.",
    code: "retrieval/search.py · retrieve",
  },
  reranking: {
    label: "Reranking",
    stages: ["reranking"],
    branch: "documents",
    kind: "RANK",
    x: 531,
    y: 394,
    does: "A cross-encoder model reads the question with each of 30 candidates and scores them. If it fails, ranking falls back to features.",
    code: "retrieval/search.py · rerank, llm/ollama.py · relevance",
  },
  evidence: {
    label: "Choosing evidence",
    stages: [],
    branch: "documents",
    kind: "EVIDENCE",
    x: 306,
    y: 492,
    does: "Names in the best passage pull in linked passages from other documents (second hop); split tables and lists are completed; the best passages are kept within the 9,000-character evidence budget.",
    code: "retrieval/hop.py, retrieval/structure.py, reasoning/evidence.py",
  },
  generating: {
    label: "Generating",
    stages: ["waiting_for_model", "generating"],
    branch: "documents",
    kind: "GENERATE",
    x: 552,
    y: 590,
    does: "The answer model reads the passages and writes the answer with the ids of the passages it relies on; the text streams to you as a draft. It is asked once more if no citation is valid or the answer is in the wrong language.",
    code: "generation/answer.py, generation/prompt.py",
  },
  validating: {
    label: "Validating",
    stages: ["validating"],
    branch: "documents",
    kind: "VALIDATE",
    x: 837,
    y: 492,
    does: "Citation ids are checked against the passages the model was actually shown. An answer with no valid citation is withheld; a model that says the passages do not answer ends in no information.",
    code: "generation/answer.py · generate_answer",
  },
  verifying: {
    label: "Verifying",
    stages: ["verifying"],
    branch: "documents",
    kind: "VERIFY",
    x: 1067,
    y: 385,
    does: "Each sentence of the answer is matched to the passages that state it: citations are completed and sentences no passage supports are marked.",
    code: "generation/attribution.py",
  },
  noinfo: {
    label: "No information",
    stages: [],
    branch: "documents",
    kind: "FALLBACK",
    x: 790,
    y: 271,
    does: "The documents do not answer the question (nothing found, the model found nothing in the passages, or no citation could be checked). The answer says so instead of guessing.",
    code: "generation/answer.py · NO_EVIDENCE",
  },
  done: {
    label: "Answer",
    stages: ["streaming"],
    kind: "RESPONSE",
    x: 1084,
    y: 160,
    does: "The question and answer are saved with their trace and metrics, then the checked answer is sent. A model failure before this point returns an error and saves nothing.",
    code: "pipeline/querying.py · chat, api/chat.py",
  },
};
const IDS = Object.keys(STATES) as StateId[];
const stateOf = (stage: string) =>
  IDS.find((id) => STATES[id].stages.includes(stage));
const STATUS_TEXT: Record<string, string> = {
  answered: "answered",
  conflicting_evidence: "sources disagree",
  insufficient_evidence: "no supporting information",
  conversation: "small talk",
};

/* canvas layout: states placed by hand like a drawn state machine, drifting left to right: small
   talk branching up, ranking and evidence looping down and back, no information off to the side */
const position = (id: StateId) => ({ x: STATES[id].x, y: STATES[id].y });

/** The transitions, with the card sides they leave and enter (t/b/l/r handles on every card). */
const TRANSITIONS: Transition<StateId>[] = [
  { from: "routing", to: "replying", label: "Small talk" },
  {
    from: "routing",
    to: "understanding",
    label: "Question",
  },
  { from: "understanding", to: "searching" },
  {
    from: "searching",
    to: "reranking",
    label: "Candidates",
  },
  {
    from: "searching",
    to: "noinfo",
    label: "Nothing found",
  },
  { from: "reranking", to: "evidence" },
  { from: "evidence", to: "generating" },
  { from: "generating", to: "validating" },
  { from: "validating", to: "verifying", label: "Cited" },
  {
    from: "validating",
    to: "noinfo",
    label: "Not answered",
  },
  { from: "verifying", to: "done" },
  { from: "noinfo", to: "done" },
  { from: "replying", to: "done" },
];

/** How one answer moved through the question state machine, on a pannable, zoomable canvas:
 *  each state a card coloured by what happened (done, running, waiting, not taken), the path taken
 *  drawn in colour. Select a state for what it does, its time, output, stage events and code. Live
 *  while the answer is produced (from the stage events), afterwards from the trace and metrics. */
export function PipelineView({
  message,
  question,
  live,
}: {
  message: Message | null;
  question: string | null;
  live: Live | null;
}) {
  const [picked, setPicked] = useState<{
    question: string | null;
    id: StateId;
  } | null>(null);
  const trace = message?.trace;
  const metrics = message?.metrics;
  const finished = !live && Boolean(message?.status);
  const noInfo = message?.status === "insufficient_evidence";

  const seen = new Set((live?.stages ?? []).map((s) => stateOf(s.stage)));
  const branch: Branch | null = metrics?.route
    ? metrics.route
    : message?.status === "conversation" || seen.has("replying")
      ? "small_talk"
      : finished || seen.has("understanding") || seen.has("searching") || trace
        ? "documents"
        : null;

  // time per state: saved stage times, or live from the arrival of each stage event
  const duration: Partial<Record<StateId, number>> = {};
  if (live) {
    live.stages.forEach((s, i) => {
      const id = stateOf(s.stage);
      if (!id) return;
      const end = live.stages[i + 1]?.at ?? live.now;
      duration[id] = (duration[id] ?? 0) + Math.max(0, end - s.at);
    });
  } else {
    for (const [stage, ms] of Object.entries(metrics?.stages_ms ?? {})) {
      const id = stateOf(stage);
      if (id) duration[id] = (duration[id] ?? 0) + ms;
    }
  }
  const total =
    trace?.timings?.total_ms ??
    message?.response_ms ??
    (live ? live.now - live.startedAt : 0);
  const activeId = live ? stateOf(live.stages.at(-1)?.stage ?? "") : undefined;

  const statusOf = (id: StateId): Status => {
    const state = STATES[id];
    if (state.branch && branch && state.branch !== branch) return "skipped";
    if (live) {
      if (id === activeId) return "active";
      if (seen.has(id) || (id === "evidence" && trace?.passages.length))
        return "done";
      if (id === "noinfo") return "pending";
      const order = IDS.indexOf(id),
        at = activeId ? IDS.indexOf(activeId) : -1;
      return order < at ? "skipped" : "pending";
    }
    if (!message) return "pending";
    if (id === "done" || id === "routing") return "done";
    if (id === "noinfo") return noInfo ? "done" : "skipped";
    if (id in duration) return "done";
    if (id === "understanding" || id === "searching")
      return trace ? "done" : "skipped";
    if (id === "evidence") return trace?.passages.length ? "done" : "skipped";
    if ((id === "generating" || id === "validating") && trace?.passages.length)
      return "done";
    return "skipped";
  };
  const status = Object.fromEntries(
    IDS.map((id) => [id, statusOf(id)]),
  ) as Record<StateId, Status>;

  // the selected state: the one picked for this question, else the running one, else the slowest
  const slowest = IDS.filter((id) => duration[id] !== undefined).sort(
    (a, b) => (duration[b] ?? 0) - (duration[a] ?? 0),
  )[0];
  const selected: StateId | undefined =
    picked && picked.question === question
      ? picked.id
      : (activeId ?? slowest ?? (message ? "routing" : undefined));
  const pick = (id: string) => {
    if (id in STATES) setPicked({ question, id: id as StateId });
  };

  const retries = metrics?.retries?.length ?? 0;
  const nodes: Node<CardData>[] = IDS.map((id) => {
    const st = status[id],
      ms = duration[id],
      sub = subLine(st, ms);
    return {
      id,
      type: "state",
      position: position(id),
      data: {
        kind: STATES[id].kind,
        label: STATES[id].label,
        status: st,
        sub,
        share:
          ms !== undefined && total > 0 && st !== "skipped"
            ? Math.min(1, ms / total)
            : null,
        note:
          id === "generating" && retries > 0
            ? `retried ×${retries}`
            : undefined,
        selected: selected === id,
      },
      ariaLabel: `${STATES[id].label}: ${sub}`,
    };
  });
  const edges = buildEdges(TRANSITIONS, status, position);

  const taken = IDS.filter((id) => status[id] !== "skipped");
  const doneCount = taken.filter((id) => status[id] === "done").length;
  const resultText = live
    ? "Running"
    : !message
      ? "Not started"
      : message.status === "conversation"
        ? "Small talk"
        : noInfo
          ? "No information"
          : message.status === "conflicting_evidence"
            ? "Sources disagree"
            : "Finished";

  return (
    <section
      className="trace-panel pipeline-panel"
      aria-label="Answer pipeline"
    >
      <header className="trace-header">
        <div className="eyebrow">HOW THE ANSWER MOVED THROUGH THE PIPELINE</div>
        <h2 dir="auto">{question ?? "Ask a question to see each step"}</h2>
      </header>

      <div className="pipe-tiles">
        <div className="pipe-tile">
          <strong>
            {message || live
              ? secs(live ? live.now - live.startedAt : total)
              : "–"}
          </strong>
          <span>Elapsed time</span>
        </div>
        <div className="pipe-tile">
          <strong>{message ? `${doneCount}/${taken.length}` : "–"}</strong>
          <span>States processed</span>
        </div>
        <div className="pipe-tile">
          <strong>
            {trace
              ? `${trace.passages.length}/${trace.counts.candidates}`
              : "–"}
          </strong>
          <span>Passages used/found</span>
        </div>
        <div className="pipe-tile">
          <strong>{resultText}</strong>
          <span>Execution status</span>
        </div>
      </div>

      <CanvasLegend />

      <StateCanvas
        key={question ?? ""}
        cards={nodes}
        edges={edges}
        focus={activeId ? position(activeId) : undefined}
        onPick={pick}
        label="Question state machine"
      />

      {selected ? (
        <Details
          id={selected}
          status={status[selected]}
          ms={duration[selected]}
          total={total}
          message={message}
          live={live}
        />
      ) : (
        <p className="trace-empty">
          <Workflow size={14} /> Ask a question to follow it through these
          states.
        </p>
      )}
    </section>
  );
}

function Details({
  id,
  status,
  ms,
  total,
  message,
  live,
}: {
  id: StateId;
  status: Status;
  ms: number | undefined;
  total: number;
  message: Message | null;
  live: Live | null;
}) {
  const state = STATES[id];
  const out =
    message && (status === "done" || status === "active")
      ? outputs(id, message, live)
      : [];
  const events = (live?.stages ?? []).filter((s) =>
    state.stages.includes(s.stage),
  );
  const saved = Object.entries(message?.metrics?.stages_ms ?? {}).filter(
    ([k]) => state.stages.includes(k),
  );
  return (
    <div
      className="pipe-details"
      role="region"
      aria-label={`${state.label} details`}
      aria-live="polite"
    >
      <div className="pipe-details-head">
        <h3>{state.label}</h3>
        <span className={`pipe-chip ${status}`}>
          {status === "done"
            ? "done"
            : status === "active"
              ? "running"
              : status === "pending"
                ? "waiting"
                : "not taken"}
        </span>
        {ms !== undefined && status !== "skipped" && (
          <span className="pipe-details-time">
            {secs(ms)}
            {total > 0 && (
              <small> · {Math.round((ms / total) * 100)}% of the answer</small>
            )}
          </span>
        )}
      </div>
      <p className="pipe-does">{state.does}</p>
      {status === "skipped" && message && (
        <p className="pipe-why">{skipReason(id, message)}</p>
      )}
      {out.length > 0 && (
        <dl className="pipeline-out">
          {out.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd dir="auto">{v}</dd>
            </div>
          ))}
        </dl>
      )}
      <dl className="pipeline-out pipe-meta">
        {state.stages.length > 0 && (
          <div>
            <dt>stage events</dt>
            <dd>
              {state.stages
                .map((s) => {
                  const ms = saved.find(([k]) => k === s)?.[1];
                  const seen = events.some((e) => e.stage === s);
                  return `${s}${ms !== undefined ? ` (${secs(ms)})` : seen ? " (seen)" : ""}`;
                })
                .join(", ")}
            </dd>
          </div>
        )}
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

function skipReason(id: StateId, message: Message): string {
  const conversation = message.status === "conversation";
  if (id === "replying")
    return "Not taken: the message was a question, not small talk.";
  if (conversation)
    return "Not taken: the message was small talk, answered without the documents.";
  if (id === "reranking")
    return "Not taken: the reranker is off, or there was nothing to rank.";
  if (id === "verifying")
    return message.status === "insufficient_evidence"
      ? "Not taken: there was no cited answer to verify."
      : "Not taken: statement verification is turned off.";
  if (id === "noinfo") return "Not taken: the documents answered the question.";
  return message.status === "insufficient_evidence"
    ? "Not taken: no passages were found for the question."
    : "Not reached.";
}

/** What one state produced, as label/value pairs; only what the API returned. */
function outputs(
  id: StateId,
  m: Message,
  live: Live | null,
): [string, ReactNode][] {
  const t = m.trace;
  const x = m.metrics;
  const docs = [...new Set((t?.passages ?? []).map((p) => p.document))];
  const rows: [string, ReactNode][] = [];
  switch (id) {
    case "routing":
      rows.push([
        "route",
        x?.route === "small_talk" || m.status === "conversation"
          ? "small talk"
          : "a question for the documents",
      ]);
      if (x?.route_score != null)
        rows.push([
          "score",
          `${x.route_score.toFixed(3)} (small talk at 0.110 or more)`,
        ]);
      break;
    case "replying":
      if (m.content) rows.push(["reply", m.content]);
      break;
    case "understanding":
      if (!t) break;
      rows.push(["searched for", <q key="q">{t.search_text}</q>]);
      if (t.rewritten)
        rows.push(["follow-up", "rewritten using the conversation"]);
      if (Object.keys(t.corrections ?? {}).length)
        rows.push([
          "spelling",
          Object.entries(t.corrections)
            .map(([a, b]) => `${a} → ${b}`)
            .join(", "),
        ]);
      rows.push(["intent", t.intent]);
      break;
    case "searching":
      if (t && t.counts.candidates) {
        rows.push(["meaning search", `${t.counts.dense} passages`]);
        rows.push(["keyword search", `${t.counts.sparse} passages`]);
        rows.push(["candidates", `${t.counts.candidates} after fusing`]);
      }
      break;
    case "reranking": {
      const u = x?.usage?.rerank;
      if (!t?.passages.length) break;
      rows.push([
        "ranking",
        t.reranked
          ? "cross-encoder model"
          : "feature ranking (reranker off or failed)",
      ]);
      if (u)
        rows.push([
          "model",
          `${u.calls} passages scored, ${u.prompt_tokens.toLocaleString("en")} tokens, ${secs(u.model_ms)} of model time`,
        ]);
      break;
    }
    case "evidence":
      if (!t?.passages.length) break;
      rows.push([
        "passages shown",
        `${t.passages.length} from ${docs.length} document${docs.length === 1 ? "" : "s"}`,
      ]);
      rows.push(["documents", docs.join(", ")]);
      if (t.bridge_terms.length)
        rows.push(["linked by", t.bridge_terms.join(", ")]);
      if (t.passages.some((p) => p.completes))
        rows.push([
          "completed",
          `${t.passages.filter((p) => p.completes).length} split table or list passages`,
        ]);
      rows.push([
        "top passages",
        t.passages
          .slice(0, 3)
          .map((p) => `${p.document}${p.heading ? ` › ${p.heading}` : ""}`)
          .join(" · "),
      ]);
      break;
    case "generating": {
      const u = x?.usage?.generate;
      if (live && m.drafting)
        rows.push(["draft", `${m.content.length} characters so far`]);
      if (x?.first_draft_ms)
        rows.push(["first text after", secs(x.first_draft_ms)]);
      if (x?.retries?.length)
        rows.push([
          "retried",
          x.retries.map((r) => r.replace(/_/g, " ")).join(", "),
        ]);
      if (u)
        rows.push([
          "model",
          `${u.calls} calls, ${u.prompt_tokens.toLocaleString("en")} tokens read, ${u.output_tokens} written`,
        ]);
      if (x?.models?.chat) rows.push(["answer model", x.models.chat]);
      break;
    }
    case "validating":
      if (!m.status) break;
      rows.push(["citations", `${m.citations?.length ?? 0} valid`]);
      rows.push(["status", STATUS_TEXT[m.status] ?? m.status]);
      if (m.warnings?.length) rows.push(["warnings", m.warnings.join(" · ")]);
      break;
    case "verifying": {
      const st = m.statements ?? [];
      if (!st.length) break;
      const unsupported = st.filter((s) => !s.sources.length);
      rows.push([
        "sentences",
        `${st.length - unsupported.length} of ${st.length} stated by a passage`,
      ]);
      if (unsupported.length)
        rows.push([
          "unsupported",
          unsupported.map((s) => `“${s.text}”`).join(" "),
        ]);
      break;
    }
    case "noinfo":
      if (m.content) rows.push(["answer", m.content]);
      if (m.warnings?.length) rows.push(["warnings", m.warnings.join(" · ")]);
      break;
    case "done":
      if (!m.status) break;
      rows.push(["result", STATUS_TEXT[m.status] ?? m.status]);
      if (m.citations?.length)
        rows.push([
          "sources",
          [...new Set(m.citations.map((c) => c.document_name))].join(", "),
        ]);
      if (m.trace?.timings)
        rows.push(["total", secs(m.trace.timings.total_ms)]);
      break;
  }
  return rows;
}
