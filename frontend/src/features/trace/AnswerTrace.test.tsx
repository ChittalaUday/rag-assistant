import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { Message, TracePassage } from "@/api/types";
import { AnswerTrace } from "./AnswerTrace";

afterEach(cleanup);

const passage = (
  id: string,
  document: string,
  heading: string,
  ranks: Record<string, number>,
) =>
  ({
    chunk_id: id,
    document_id: document,
    document,
    heading,
    score: 0.5,
    ranks,
  }) as TracePassage;

const answer: Message = {
  role: "assistant",
  content: "Mira leads Acme. Acme was founded on the moon.",
  status: "answered",
  citations: [
    {
      chunk_id: "c1",
      document_id: "roster.md",
      document_name: "roster.md",
      text: "",
      quote: null,
      page: null,
    },
  ],
  statements: [
    { text: "Mira leads Acme.", sources: ["c1", "c3"] },
    { text: "Acme was founded on the moon.", sources: [] },
  ],
  trace: {
    search_text: "who leads acme",
    rewritten: true,
    corrections: { aceme: "acme" },
    intent: "relationship",
    counts: { dense: 30, sparse: 12, candidates: 34 },
    reranked: true,
    bridge_terms: ["mira"],
    passages: [
      passage("c1", "roster.md", "Roster", { dense: 1, sparse: 2 }),
      passage("c2", "roster.md", "Teams", { sparse: 4 }),
      passage("c3", "people.md", "People", { hop: 1 }),
    ],
  },
};

function show(
  message: Message | null = answer,
  preview = null as Parameters<typeof AnswerTrace>[0]["preview"],
) {
  const onSource = vi.fn();
  render(
    <AnswerTrace
      message={message}
      question="Who leads Aceme?"
      preview={preview}
      onSource={onSource}
    />,
  );
  return onSource;
}

it("explains an empty panel before any answer", () => {
  show(null);
  expect(
    screen.getByText(/Every answer shows its trail here/),
  ).toBeInTheDocument();
});

it("shows how the question was read and searched", () => {
  show();
  expect(screen.getByText("who leads acme")).toBeInTheDocument();
  expect(screen.getByText("follow-up made self-contained")).toBeInTheDocument();
  expect(screen.getByText("aceme → acme")).toBeInTheDocument();
  expect(
    screen.getByText(/30 passages by meaning · 12 by keywords → 34 candidates/),
  ).toHaveTextContent("re-ranked by a relevance model");
  expect(
    screen.getByText(/Followed “mira” to other documents/),
  ).toBeInTheDocument();
});

it("groups the chosen passages by document, numbers them and marks what was cited", async () => {
  const onSource = show();
  expect(screen.getByText(/3 passages from 2 documents/)).toBeInTheDocument();
  const first = screen.getByRole("button", {
    name: "Passage 1 from roster.md, Roster, cited",
  });
  expect(within(first).getByText("meaning · keywords")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Passage 3 from people.md, People" }),
  ).toHaveTextContent("second hop");
  await userEvent.click(first);
  expect(onSource).toHaveBeenCalledWith("c1");
});

it("links each sentence to the passages that state it and flags unsupported ones", async () => {
  const onSource = show();
  expect(
    screen.getByText(/1 of 2 sentences matched to a passage/),
  ).toBeInTheDocument();
  const supported = screen.getByText("Mira leads Acme.").closest("li")!;
  const refs = within(supported).getAllByRole("button");
  expect(refs.map((b) => b.textContent)).toEqual(["1", "3"]);
  await userEvent.click(refs[1]);
  expect(onSource).toHaveBeenCalledWith("c3");
  const unsupported = screen
    .getByText("Acme was founded on the moon.")
    .closest("li")!;
  expect(unsupported).toHaveClass("unsupported");
  expect(unsupported).toHaveTextContent("no passage states this");
});

it("highlights linked sentences and passages on hover and focus", async () => {
  show();
  const sentence = screen.getByText("Mira leads Acme.").closest("li")!;
  const people = screen.getByRole("button", {
    name: /Passage 3 from people.md/,
  });
  const teams = screen.getByRole("button", {
    name: /Passage 2 from roster.md/,
  });
  await userEvent.hover(sentence);
  expect(people).toHaveClass("lit");
  expect(teams).not.toHaveClass("lit");
  await userEvent.unhover(sentence);
  expect(people).not.toHaveClass("lit");
  act(() => people.focus());
  expect(sentence).toHaveClass("lit");
});

it("lists likely sources while typing and opens one", async () => {
  const onSource = show(null, {
    passages: [
      {
        chunk_id: "c9",
        document_id: "d",
        document: "leave.md",
        heading: "Approvals",
      },
    ],
    corrections: { aproval: "approval" },
  });
  expect(
    screen.getByText(/Likely sources for what you are typing/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/“approval” \(you typed “aproval”\)/),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /leave.md/ }));
  expect(onSource).toHaveBeenCalledWith("c9");
});

it("says plainly when nothing was claimed", () => {
  show({ ...answer, status: "insufficient_evidence", statements: [] });
  expect(
    screen.getByText(/No passage answered the question/),
  ).toBeInTheDocument();
});

it("shows the start of each passage, with its section underneath", () => {
  const withSnippet: Message = {
    ...answer,
    trace: {
      ...answer.trace!,
      passages: [
        {
          ...answer.trace!.passages[0],
          snippet: "Mira leads Acme and its two teams.",
        },
      ],
    },
  };
  show(withSnippet);
  const first = screen.getByRole("button", {
    name: /Passage 1 from roster.md/,
  });
  expect(
    within(first).getByText("Mira leads Acme and its two teams."),
  ).toBeInTheDocument();
  expect(
    within(first).getByText("Roster · meaning · keywords"),
  ).toBeInTheDocument();
});
