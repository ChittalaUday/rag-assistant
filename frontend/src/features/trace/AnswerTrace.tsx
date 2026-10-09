import { useMemo, useState } from "react";
import {
  FileText,
  Route,
  Search,
  ListChecks,
  MessageSquareText,
} from "lucide-react";
import type { Message, SearchPreview, TracePassage } from "@/api/types";

const VIA: Record<string, string> = {
  dense: "meaning",
  sparse: "keywords",
  hop: "second hop",
  graph: "linked names",
};

function viaLabels(passage: TracePassage): string[] {
  const labels = Object.keys(passage.ranks ?? {}).map((k) => VIA[k] ?? k);
  if (passage.completes) labels.push(`completes ${passage.completes}`);
  return labels;
}

type Focus =
  { kind: "statement"; index: number } | { kind: "passage"; id: string } | null;

/** How one answer was produced: what was searched, which passages were chosen, and which
 *  passages state each sentence of the answer. Built only from data the API returned. */
export function AnswerTrace({
  message,
  question,
  preview,
  onSource,
}: {
  message: Message | null;
  question: string | null;
  preview: SearchPreview | null;
  onSource: (chunkId: string) => void;
}) {
  const [focus, setFocus] = useState<Focus>(null);
  const trace = message?.trace;
  const statements = message?.statements ?? [];
  const cited = useMemo(
    () => new Set((message?.citations ?? []).map((c) => c.chunk_id)),
    [message],
  );
  const number = useMemo(
    () => new Map((trace?.passages ?? []).map((p, i) => [p.chunk_id, i + 1])),
    [trace],
  );
  const groups = useMemo(() => {
    const byDoc = new Map<string, { name: string; passages: TracePassage[] }>();
    for (const p of trace?.passages ?? []) {
      const group = byDoc.get(p.document_id) ?? {
        name: p.document,
        passages: [],
      };
      group.passages.push(p);
      byDoc.set(p.document_id, group);
    }
    return [...byDoc.values()];
  }, [trace]);
  const lit = (chunkId: string) =>
    focus?.kind === "passage"
      ? focus.id === chunkId
      : focus?.kind === "statement"
        ? (statements[focus.index]?.sources ?? []).includes(chunkId)
        : false;
  const statementLit = (index: number) =>
    focus?.kind === "statement"
      ? focus.index === index
      : focus?.kind === "passage"
        ? (statements[index]?.sources ?? []).includes(focus.id)
        : false;

  return (
    <section className="trace-panel" aria-label="How the answer was built">
      <header className="trace-header">
        <div className="eyebrow">HOW THIS ANSWER WAS BUILT</div>
        <h2>{question ?? "Ask a question to see its trail"}</h2>
      </header>

      {preview && preview.passages.length > 0 && (
        <div className="trace-preview" aria-live="polite">
          <div className="trace-step-title">
            <Search size={13} /> Likely sources for what you are typing
          </div>
          <ul>
            {preview.passages.map((p) => (
              <li key={p.chunk_id}>
                <button type="button" onClick={() => onSource(p.chunk_id)}>
                  <FileText size={12} />
                  <span>{p.document}</span>
                  {p.heading && <small>{p.heading}</small>}
                </button>
              </li>
            ))}
          </ul>
          {Object.keys(preview.corrections).length > 0 && (
            <p className="trace-note">
              Will search for{" "}
              {Object.entries(preview.corrections)
                .map(([from, to]) => `“${to}” (you typed “${from}”)`)
                .join(", ")}
            </p>
          )}
        </div>
      )}

      {!trace ? (
        <p className="trace-empty">
          Every answer shows its trail here: how your question was read, how
          many passages each search found, the passages chosen from each
          document, and which passage states each sentence of the answer.
        </p>
      ) : (
        <ol className="trace-steps">
          <li className="trace-step">
            <div className="trace-step-title">
              <Route size={13} /> Understood
            </div>
            <p>
              Searched for <q>{trace.search_text}</q>
            </p>
            <div className="trace-chips">
              {trace.rewritten && (
                <span className="chip">follow-up made self-contained</span>
              )}
              {Object.entries(trace.corrections ?? {}).map(([from, to]) => (
                <span className="chip" key={from}>
                  {from} → {to}
                </span>
              ))}
            </div>
          </li>
          <li className="trace-step">
            <div className="trace-step-title">
              <Search size={13} /> Searched
            </div>
            <p>
              {trace.counts.dense} passages by meaning · {trace.counts.sparse}{" "}
              by keywords → {trace.counts.candidates} candidates
              {trace.reranked ? ", re-ranked by a relevance model" : ""}
            </p>
            {trace.bridge_terms.length > 0 && (
              <p className="trace-note">
                Followed {trace.bridge_terms.map((t) => `“${t}”`).join(", ")} to
                other documents
              </p>
            )}
          </li>
          <li className="trace-step">
            <div className="trace-step-title">
              <ListChecks size={13} /> Chosen · {trace.passages.length} passages
              from {groups.length} document{groups.length === 1 ? "" : "s"}
            </div>
            {groups.map((group) => (
              <div className="trace-doc" key={group.name}>
                <div className="trace-doc-name">
                  <FileText size={12} /> {group.name}
                </div>
                {group.passages.map((p) => (
                  <button
                    type="button"
                    key={p.chunk_id}
                    className={`trace-passage ${cited.has(p.chunk_id) ? "cited" : ""} ${lit(p.chunk_id) ? "lit" : ""}`}
                    onMouseEnter={() =>
                      setFocus({ kind: "passage", id: p.chunk_id })
                    }
                    onMouseLeave={() => setFocus(null)}
                    onFocus={() =>
                      setFocus({ kind: "passage", id: p.chunk_id })
                    }
                    onBlur={() => setFocus(null)}
                    onClick={() => onSource(p.chunk_id)}
                    aria-label={`Passage ${number.get(p.chunk_id)} from ${group.name}${p.heading ? `, ${p.heading}` : ""}${cited.has(p.chunk_id) ? ", cited" : ""}`}
                  >
                    <span className="trace-num">{number.get(p.chunk_id)}</span>
                    <span className="trace-passage-body">
                      <span>{p.snippet || p.heading || "Passage"}</span>
                      <small>
                        {[p.snippet ? p.heading : null, ...viaLabels(p)]
                          .filter(Boolean)
                          .join(" · ")}
                      </small>
                    </span>
                    {cited.has(p.chunk_id) && (
                      <span className="chip cited-chip">cited</span>
                    )}
                  </button>
                ))}
              </div>
            ))}
          </li>
          <li className="trace-step">
            <div className="trace-step-title">
              <MessageSquareText size={13} /> Answered
              {statements.length > 0 &&
                ` · ${statements.filter((s) => s.sources.length).length} of ${statements.length} sentences matched to a passage`}
            </div>
            {statements.length === 0 ? (
              <p className="trace-note">
                {message?.status === "insufficient_evidence"
                  ? "No passage answered the question, so nothing was claimed."
                  : "Sentence-level matching is not available for this answer."}
              </p>
            ) : (
              <ul className="trace-statements">
                {statements.map((s, i) => (
                  <li
                    key={i}
                    className={`trace-statement ${s.sources.length ? "" : "unsupported"} ${statementLit(i) ? "lit" : ""}`}
                    onMouseEnter={() =>
                      setFocus({ kind: "statement", index: i })
                    }
                    onMouseLeave={() => setFocus(null)}
                  >
                    <span>{s.text}</span>
                    <span className="trace-refs">
                      {s.sources.length ? (
                        s.sources.map((id) => (
                          <button
                            type="button"
                            key={id}
                            className="trace-num"
                            onClick={() => onSource(id)}
                            aria-label={`Open passage ${number.get(id) ?? ""}`}
                          >
                            {number.get(id) ?? "·"}
                          </button>
                        ))
                      ) : (
                        <small>no passage states this</small>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </li>
        </ol>
      )}
    </section>
  );
}
