import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
vi.mock("sigma", () => ({ default: vi.fn() }));
import { Relationships } from "./GraphMap";
it("opens the exact evidence attached to a relationship using keyboard controls", async () => {
  const select = vi.fn();
  const evidence = {
    chunk_id: "c1",
    document_id: "d1",
    document_name: "Policy.txt",
    text: "Long source passage.",
    quote: "Exact relation quote.",
    page: null,
  };
  render(
    <Relationships
      graph={{
        nodes: [
          { id: "a", label: "Team A", type: "team", document_ids: ["d1"] },
          { id: "b", label: "Team B", type: "team", document_ids: ["d1"] },
        ],
        edges: [
          {
            id: "e",
            source: "a",
            target: "b",
            label: "supports",
            evidence: [evidence],
          },
        ],
        highlighted_edge_ids: ["e"],
        highlighted_node_ids: [],
      }}
      onEvidence={select}
    />,
  );
  const user = userEvent.setup();
  await user.tab();
  await user.keyboard("{Enter}");
  expect(select).toHaveBeenCalledWith(evidence);
});
