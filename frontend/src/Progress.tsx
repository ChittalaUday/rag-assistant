import { LoaderCircle } from "lucide-react";
import type { Document } from "./types";
import { Progress } from "./components/animate-ui/components/radix/progress";
export function Activity({
  message,
  label,
}: {
  message: string;
  label: string;
}) {
  return (
    <div className="activity" role="status" aria-label={label}>
      <LoaderCircle size={15} className="spin" aria-hidden="true" />
      <span>{message}</span>
      <span className="activity-dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
    </div>
  );
}
export function DocumentProgress({ document }: { document: Document }) {
  const pending =
    document.status === "queued" || document.status === "processing";
  const message =
    document.processing_message ||
    (document.status === "queued"
      ? "Waiting for the document worker."
      : document.status === "processing"
        ? "Processing document; waiting for a stage update."
        : "");
  const total = document.total_chunks;
  const completed = document.processed_chunks;
  if (!pending && !message) return null;
  return (
    <div className="document-progress">
      {pending ? (
        <Activity label={`Processing ${document.name}`} message={message} />
      ) : (
        <p>{message}</p>
      )}
      {pending &&
        typeof total === "number" &&
        total > 0 &&
        typeof completed === "number" && (
          <>
            <Progress
              aria-label={`Chunk progress for ${document.name}`}
              max={total}
              value={Math.max(0, Math.min(completed, total))}
              aria-valuetext={`${completed} of ${total} chunks in ${document.processing_stage ?? "the current stage"}`}
            />
            <small>
              {completed} of {total} chunks
            </small>
          </>
        )}
    </div>
  );
}
