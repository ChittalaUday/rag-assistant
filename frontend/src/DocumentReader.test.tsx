import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { DocumentReader } from "./DocumentReader";
import type { DocumentContent } from "./types";
vi.mock("./api", () => ({ api: { documentContent: vi.fn() } }));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
const fixture = (id: string): DocumentContent => ({
  document: {
    id,
    name: `Full ${id}.md`,
    content_hash: `hash-${id}`,
    status: "ready",
    chunk_count: 2,
    created_at: "2026-10-05",
    error: null,
    warnings: [],
  },
  text: `Snapshot date: 2026-10-05\nFull document ${id} including content beyond the cited passage.\n<script>untrusted()</script>`,
  chunks: [],
  text_kind: "extracted",
  source_urls: ["https://example.org/source", "javascript:alert(1)"],
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
it("shows full text and metadata rather than just a citation, with safe original sources", async () => {
  vi.mocked(api.documentContent).mockResolvedValue(fixture("A"));
  render(<DocumentReader id="A" name="Requested name" onClose={() => {}} />);
  expect(
    screen.getByRole("status", { name: "Document loading" }),
  ).toHaveTextContent("Loading full document");
  expect(
    await screen.findByText(/including content beyond the cited passage/),
  ).toBeInTheDocument();
  expect(screen.getByText("hash-A")).toBeInTheDocument();
  expect(screen.getByText("2026-10-05")).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "Full A.md" }),
  ).toBeInTheDocument();
  expect(screen.getAllByRole("link")).toHaveLength(1);
  expect(screen.getByRole("link")).toHaveAttribute(
    "href",
    "https://example.org/source",
  );
  expect(document.querySelector(".document-reader script")).toBeNull();
});
it("ignores older reader responses when document choice changes", async () => {
  const a = deferred<DocumentContent>();
  const b = deferred<DocumentContent>();
  vi.mocked(api.documentContent).mockImplementation((id) =>
    id === "A" ? a.promise : b.promise,
  );
  const { rerender } = render(
    <DocumentReader id="A" name="A" onClose={() => {}} />,
  );
  rerender(<DocumentReader id="B" name="B" onClose={() => {}} />);
  await act(async () => b.resolve(fixture("B")));
  await act(async () => a.resolve(fixture("A")));
  expect(
    screen.getByRole("heading", { name: "Full B.md" }),
  ).toBeInTheDocument();
  expect(screen.queryByText("hash-A")).not.toBeInTheDocument();
});
it("does not reopen a closed reader after a pending response completes", async () => {
  const pending = deferred<DocumentContent>();
  vi.mocked(api.documentContent).mockReturnValue(pending.promise);
  const { rerender } = render(
    <DocumentReader id="A" name="A" onClose={() => {}} />,
  );
  rerender(<DocumentReader id={null} name="" onClose={() => {}} />);
  await act(async () => pending.resolve(fixture("A")));
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
});
it("shows actionable full document failures", async () => {
  vi.mocked(api.documentContent).mockRejectedValue(
    new Error("Document was deleted"),
  );
  render(<DocumentReader id="A" name="A" onClose={() => {}} />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Document was deleted",
  );
});
it("discloses when a failed replacement still shows the last published version", async () => {
  vi.mocked(api.documentContent).mockResolvedValue({
    ...fixture("A"),
    showing_last_published_version: true,
    provenance_note:
      "Showing the last published document while replacement is processing.",
  });
  render(<DocumentReader id="A" name="A" onClose={() => {}} />);
  expect(await screen.findByRole("note")).toHaveTextContent(
    "Showing the last published document while replacement is processing.",
  );
});

it("refreshes a selected document after publication without replacing its displayed text during loading", async () => {
  const pending = deferred<DocumentContent>();
  vi.mocked(api.documentContent)
    .mockResolvedValueOnce(fixture("A"))
    .mockReturnValueOnce(pending.promise);
  const { rerender } = render(
    <DocumentReader id="A" name="A" revision="processing" onClose={() => {}} />,
  );
  expect(await screen.findByText("hash-A")).toBeInTheDocument();
  rerender(
    <DocumentReader
      id="A"
      name="A"
      revision="ready-new-hash"
      onClose={() => {}}
    />,
  );
  expect(screen.getByText("hash-A")).toBeInTheDocument();
  expect(
    screen.getByRole("status", { name: "Document loading" }),
  ).toHaveTextContent("Refreshing");
  await act(async () =>
    pending.resolve({
      ...fixture("A"),
      document: {
        ...fixture("A").document,
        content_hash: "published-new-hash",
      },
      text: "Newly published full document text.",
    }),
  );
  expect(screen.getByText("published-new-hash")).toBeInTheDocument();
  expect(
    screen.getByText("Newly published full document text."),
  ).toBeInTheDocument();
});
it("keeps the last loaded document visible with an error when refreshing fails", async () => {
  vi.mocked(api.documentContent)
    .mockResolvedValueOnce(fixture("A"))
    .mockRejectedValueOnce(new Error("Unable to refresh local metadata"));
  const { rerender } = render(
    <DocumentReader id="A" name="A" revision="initial" onClose={() => {}} />,
  );
  expect(await screen.findByText("hash-A")).toBeInTheDocument();
  rerender(
    <DocumentReader id="A" name="A" revision="updated" onClose={() => {}} />,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Unable to refresh local metadata",
  );
  expect(screen.getByText("hash-A")).toBeInTheDocument();
});
it("ignores a superseded refresh response for the same document", async () => {
  const older = deferred<DocumentContent>();
  const newer = deferred<DocumentContent>();
  vi.mocked(api.documentContent)
    .mockResolvedValueOnce(fixture("A"))
    .mockReturnValueOnce(older.promise)
    .mockReturnValueOnce(newer.promise);
  const { rerender } = render(
    <DocumentReader id="A" name="A" revision="initial" onClose={() => {}} />,
  );
  expect(await screen.findByText("hash-A")).toBeInTheDocument();
  rerender(
    <DocumentReader
      id="A"
      name="A"
      revision="older-stage"
      onClose={() => {}}
    />,
  );
  rerender(
    <DocumentReader
      id="A"
      name="A"
      revision="newer-stage"
      onClose={() => {}}
    />,
  );
  await act(async () =>
    newer.resolve({
      ...fixture("A"),
      document: { ...fixture("A").document, content_hash: "newest-published" },
    }),
  );
  await act(async () =>
    older.resolve({
      ...fixture("A"),
      document: {
        ...fixture("A").document,
        content_hash: "superseded-published",
      },
    }),
  );
  expect(screen.getByText("newest-published")).toBeInTheDocument();
  expect(screen.queryByText("superseded-published")).not.toBeInTheDocument();
});
