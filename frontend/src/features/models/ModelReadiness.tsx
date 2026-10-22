import { Cpu } from "lucide-react";
import type {
  AnswerChoice,
  AnswerModels,
  ModelRole,
  ModelsStatus,
  ModelState,
} from "@/api/types";
import { Activity } from "@/features/documents/Progress";
import { Button } from "@/components/animate-ui/components/buttons/button";

const LABEL: Record<ModelRole, string> = {
  chat: "Answers",
  embedding: "Search",
  rerank: "Ranking",
  context: "Indexing",
};
// what one question uses; the context model only runs while documents are indexed
const FOR_QUESTIONS: ModelRole[] = ["chat", "embedding", "rerank"];

/** Models a question needs: to load or wake, not installed, or whose server cannot be reached. */
export function questionModels(status: ModelsStatus | null) {
  const used = (status?.models ?? []).filter((m) =>
    FOR_QUESTIONS.includes(m.role),
  );
  return {
    cold: used.filter(
      (m) => !m.loaded && m.installed && m.state !== "unreachable",
    ),
    absent: used.filter((m) => m.state === "not_installed"),
    unreachable: used.filter((m) => m.state === "unreachable"),
  };
}

function seconds(m: { loading_seconds?: number | null }) {
  return m.loading_seconds != null ? ` ${m.loading_seconds} s` : "";
}
function stateText(m: {
  state: ModelState["state"];
  loading_seconds?: number | null;
}) {
  switch (m.state) {
    case "loaded":
      return "loaded";
    case "loading":
      return `loading…${seconds(m)}`;
    case "asleep":
      return "asleep";
    case "unreachable":
      return "unreachable";
    case "not_installed":
      return "not installed";
    default:
      return "not loaded";
  }
}
const where = (m: ModelState) => (m.where === "server" ? " on the server" : "");

const PROVIDER = { vllm: "Model server (vLLM)", ollama: "Ollama (local)" };
const same = (a: AnswerChoice | null, b: AnswerChoice) =>
  a?.provider === b.provider && a.model === b.model;

/** Every model that can write answers, from the model server and from Ollama; picking one
 * answers the next questions with it. `choice` null: the default model. */
export function AnswerModelPicker({
  available,
  choice,
  onChoose,
}: {
  available: AnswerModels | null;
  choice: AnswerChoice | null;
  onChoose: (choice: AnswerChoice | null) => void;
}) {
  if (!available?.models.length) return null;
  const shown =
    available.models.find((m) => same(choice, m)) ??
    available.models.find((m) => m.default) ??
    available.models[0];
  return (
    <label
      className="answer-picker"
      title={available.errors.join("\n") || "The model that writes answers"}
    >
      <span>Model</span>
      <select
        aria-label="Answer model"
        value={`${shown.provider}|${shown.model}`}
        onChange={(e) => {
          const [provider, ...rest] = e.target.value.split("|");
          const model = rest.join("|");
          const picked = available.models.find(
            (m) => m.provider === provider && m.model === model,
          );
          onChoose(picked && !picked.default ? picked : null);
        }}
      >
        {(["vllm", "ollama"] as const).map((provider) => {
          const group = available.models.filter((m) => m.provider === provider);
          return group.length ? (
            <optgroup key={provider} label={PROVIDER[provider]}>
              {group.map((m) => (
                <option key={m.model} value={`${m.provider}|${m.model}`}>
                  {m.model} · {stateText(m)}
                  {m.default ? " (default)" : ""}
                </option>
              ))}
            </optgroup>
          ) : null;
        })}
      </select>
    </label>
  );
}

export function ModelChips({ status }: { status: ModelsStatus | null }) {
  if (!status?.models.length) return null;
  return (
    <div className="model-chips">
      <Cpu size={12} aria-hidden="true" />
      {status.models.map((m) => (
        <span
          key={m.role}
          className={`model-chip ${m.state}`}
          title={`${m.model}${where(m)}: ${stateText(m)}${m.detail ? ` (${m.detail})` : ""}`}
        >
          <i aria-hidden="true" />
          {LABEL[m.role]}
          <small>{stateText(m)}</small>
        </span>
      ))}
    </div>
  );
}

export function ModelReadiness({
  status,
  loading,
  waitingQuestion,
  autoLoad,
  onAutoLoad,
  onLoad,
  onCancel,
}: {
  status: ModelsStatus | null;
  loading: boolean;
  waitingQuestion: boolean; // a question was sent while its models were not loaded
  autoLoad: boolean;
  onAutoLoad: (value: boolean) => void;
  onLoad: () => void;
  onCancel: () => void;
}) {
  // Ollama being down is the offline banner's job; a model server can still be asleep or loading
  if (!status || (!status.ollama_available && !status.models.length))
    return null;
  const { cold, absent, unreachable } = questionModels(status);
  const names = (list: ModelState[]) => list.map((m) => m.model).join(", ");
  const loadingNow = status.models.filter((m) => m.state === "loading");
  const asleep = cold.filter((m) => m.state === "asleep");
  const many = (list: ModelState[]) => list.length > 1;
  const verb = asleep.length && asleep.length === cold.length ? "Wake" : "Load";
  return (
    <section className="model-bar" aria-label="Models">
      {absent.length > 0 && (
        <p role="alert" className="model-note missing">
          {names(absent)} {many(absent) ? "are" : "is"} not installed.
          Installing is explicit: run <code>ollama pull {absent[0].model}</code>
          {many(absent) ? " (and the others)" : ""}, then check again.
        </p>
      )}
      {unreachable.length > 0 && (
        <p role="alert" className="model-note missing">
          Cannot reach the model server for {names(unreachable)}
          {unreachable[0].detail ? `: ${unreachable[0].detail}` : "."}
        </p>
      )}
      {loading || loadingNow.length > 0 ? (
        <Activity
          label="Loading models"
          message={`Loading ${names(loadingNow.length ? loadingNow : cold) || "models"}${where(loadingNow[0] ?? cold[0] ?? ({ where: "local" } as ModelState))} into memory…${
            loadingNow[0] ? seconds(loadingNow[0]) : ""
          }${loadingNow.some((m) => m.where === "server") ? " (a sleeping model takes a few minutes)" : ""}`}
        />
      ) : (
        cold.length > 0 &&
        absent.length === 0 &&
        unreachable.length === 0 && (
          <div className="model-prompt" role="status">
            <span>
              {names(cold)}
              {where(cold[0])} {many(cold) ? "are" : "is"}{" "}
              {asleep.length ? "asleep to free memory" : "not loaded in memory"}
              .{" "}
              {waitingQuestion
                ? `${verb} ${many(cold) ? "them" : "it"} to answer your question.`
                : `The first question would wait while ${many(cold) ? "they load" : "it loads"}${
                    asleep.length ? " (a few minutes)" : ""
                  }.`}
            </span>
            <Button size="sm" onClick={onLoad}>
              {waitingQuestion ? `${verb} and send` : `${verb} now`}
            </Button>
            {waitingQuestion && (
              <Button size="sm" variant="ghost" onClick={onCancel}>
                Cancel
              </Button>
            )}
          </div>
        )
      )}
      <label className="model-auto">
        <input
          type="checkbox"
          checked={autoLoad}
          onChange={(e) => onAutoLoad(e.target.checked)}
        />
        Load models automatically when a question needs them
      </label>
    </section>
  );
}
