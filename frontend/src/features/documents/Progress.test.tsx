import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { DocumentProgress } from "./Progress";
import type { Document } from "@/api/types";
afterEach(cleanup);
const document: Document = {
  id: "d",
  name: "Policy.txt",
  content_hash: "hash",
  status: "processing",
  chunk_count: 0,
  created_at: "",
  error: null,
  warnings: [],
  processing_stage: "embedding",
  processing_message: "Embedding document chunks with the local model.",
  processed_chunks: 2,
  total_chunks: 5,
};
it("shows the actual backend stage and measured progress without simulated completion", () => {
  render(<DocumentProgress document={document} />);
  expect(screen.getByRole("status")).toHaveTextContent(
    document.processing_message!,
  );
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "2");
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuemax", "5");
  expect(screen.getByText("2 of 5 chunks")).toBeInTheDocument();
});
it("uses honest indeterminate text when counts are unavailable", () => {
  render(
    <DocumentProgress
      document={{
        ...document,
        processing_message: null,
        processed_chunks: undefined,
        total_chunks: undefined,
      }}
    />,
  );
  expect(screen.getByRole("status")).toHaveTextContent(
    "waiting for a stage update",
  );
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
});

it("updates measured per-stage counts without implying overall document completion", () => {
  const { rerender } = render(<DocumentProgress document={document} />);
  const progress = screen.getByRole("progressbar");
  expect(progress).toHaveAccessibleName("Chunk progress for Policy.txt");
  expect(progress).toHaveAttribute(
    "aria-valuetext",
    "2 of 5 chunks in embedding",
  );
  rerender(
    <DocumentProgress
      document={{
        ...document,
        processing_stage: "extracting",
        processing_message: "Extracting relationships.",
        processed_chunks: 1,
        total_chunks: 5,
      }}
    />,
  );
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "1");
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuemax", "5");
  expect(screen.getByRole("progressbar")).toHaveAttribute(
    "aria-valuetext",
    "1 of 5 chunks in extracting",
  );
  expect(screen.getByRole("status")).toHaveTextContent(
    "Extracting relationships.",
  );
});
