import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
  DialogClose,
} from "./components/animate-ui/components/radix/dialog";
import { Button } from "./components/animate-ui/components/buttons/button";
import { X } from "lucide-react";
import { api } from "./api";
import type { DocumentContent } from "./types";
import { Activity, DocumentProgress } from "./Progress";
function safeUrls(content: DocumentContent): string[] {
  const urls = [
    ...(content.source_urls ?? []),
    ...content.chunks.flatMap((c) => c.source_urls ?? []),
    ...(content.text.match(/\bhttps?:\/\/[^\s<>()[\]{}"'`]+/gi) ?? []),
  ];
  return [
    ...new Set(
      urls.flatMap((value) => {
        try {
          const url = new URL(value.replace(/[.,;:!?)\]}'"]+$/, ""));
          return ["http:", "https:"].includes(url.protocol)
            ? [url.toString()]
            : [];
        } catch {
          return [];
        }
      }),
    ),
  ];
}
export function DocumentReader({
  id,
  name,
  onClose,
  revision,
}: {
  id: string | null;
  name: string;
  onClose: () => void;
  revision?: string;
}) {
  const [content, setContent] = useState<DocumentContent | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let active = true;
    setContent((current) => (current?.document.id === id ? current : null));
    setError("");
    if (!id) {
      setLoading(false);
      return;
    }
    setLoading(true);
    void api
      .documentContent(id)
      .then((result) => {
        if (active) setContent(result);
      })
      .catch((e) => {
        if (active)
          setError(
            e instanceof Error ? e.message : "Unable to read this document",
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [id, revision]);
  const doc = content?.document;
  return (
    <Dialog
      open={Boolean(id)}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent
        from="right"
        showCloseButton={false}
        className="source-drawer document-reader left-auto max-w-none translate-x-0 translate-y-0 gap-0 rounded-none"
      >
        <div className="drawer-header">
          <div className="eyebrow">FULL DOCUMENT</div>
          <DialogClose asChild>
            <Button variant="ghost" size="icon" aria-label="Close document">
              <X size={20} />
            </Button>
          </DialogClose>
        </div>
        <DialogTitle>{doc?.name ?? name}</DialogTitle>
        <DialogDescription className="muted">
          The full locally ingested text, shown as plain text. This view does
          not execute document markup.
        </DialogDescription>
        {loading && (
          <Activity
            message={
              content
                ? "Refreshing the locally stored document…"
                : "Loading full document from local storage…"
            }
            label="Document loading"
          />
        )}
        {error && (
          <p role="alert" className="reader-error">
            {error}
          </p>
        )}
        {content && doc && (
          <>
            <dl className="document-metadata">
              <dt>Status</dt>
              <dd>{doc.status}</dd>
              <dt>Chunks</dt>
              <dd>{doc.chunk_count}</dd>
              <dt>Added</dt>
              <dd>{doc.created_at}</dd>
              <dt>SHA-256</dt>
              <dd>{doc.content_hash}</dd>
              <dt>Text form</dt>
              <dd>
                {content.text_kind === "reconstructed"
                  ? "Reconstructed from retained chunks; overlaps may repeat."
                  : content.text_kind === "original"
                    ? "Original plain-text document"
                    : "Full extracted document text"}
              </dd>
            </dl>
            {content.provenance_note && (
              <p className="reader-provenance" role="note">
                {content.provenance_note}
              </p>
            )}
            <DocumentProgress document={doc} />
            {doc.error && <p className="reader-error">{doc.error}</p>}
            {doc.warnings.map((warning, index) => (
              <p className="answer-warning" key={index}>
                {warning}
              </p>
            ))}
            <div className="source-websites">
              {safeUrls(content).map((url) => (
                <a
                  className="source-link"
                  href={url}
                  target="_blank"
                  rel="noreferrer noopener"
                  key={url}
                >
                  Source website · {new URL(url).hostname}
                </a>
              ))}
            </div>
            <h3>Full ingested text</h3>
            <pre>
              {content.text ||
                "No extracted text is available yet. Processing must finish first."}
            </pre>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
