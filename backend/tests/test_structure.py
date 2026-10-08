"""Answers keep split tables whole, never name documents, and spelling fixes stay conservative."""

import pytest
from fakes import FakeModel, ready
from helpers import index_text, make_ctx
from knowledge_engine.generation.answer import strip_document_names
from knowledge_engine.generation.prompt import INSTRUCTION
from knowledge_engine.ingestion.chunker import chunk_document
from knowledge_engine.retrieval.analyzer import analyze, correct_spelling
from knowledge_engine.retrieval.search import retrieve
from knowledge_engine.retrieval.structure import continues
from test_api import client, upload

TABLE = (
    "# Ratings\n\nThe manager classifies each performance with the prescribed ratings given below, "
    "using the parameters set for the employee.\n\n| Score | Criteria | Hike |\n| --- | --- | --- |\n"
    "| < 60% | Unacceptable performance, immediate and strict corrective action | 0% |\n"
    "| 60% - 75% | Improvement needed, can be improved with appropriate corrective actions | Up to 2% |\n"
    "| 75% - 85% | Up to par, performance meets the standards expected of positions | 2% - 5% |\n"
    "| 85% - 90% | Exceeds expectations in most quarterly goals and shows steady growth | 5% - 6% |\n"
    "| 90% - 95% | Exceeds expectations in nearly every goal and mentors colleagues well | 6% - 7% |\n"
    "| Above 95% | Outstanding, the highest possible standard of performance | 7% - 10% |\n"
)


def chunk(text, heading="H"):
    return {"text": text, "heading": heading}


def test_a_table_or_list_cut_in_two_is_recognised_as_one():
    assert continues(chunk("intro\n| a | b |\n| 1 | 2 |"), chunk("| 3 | 4 |\n| 5 | 6 |"))
    assert continues(chunk("Steps:\n1. open\n2. check"), chunk("2. check\n3. close"))
    assert not continues(chunk("| a | b |\n| 1 | 2 |"), chunk("A new paragraph."))
    assert not continues(chunk("Prose only."), chunk("| 3 | 4 |"))
    assert not continues(chunk("| a |", "One"), chunk("| b |", "Another section"))


async def test_retrieval_brings_the_other_half_of_a_split_table(tmp_path):
    chunks = chunk_document(TABLE)
    assert (
        len(chunks) >= 2
        and "Outstanding" in chunks[-1].text
        and "Outstanding" not in chunks[0].text
    )
    ctx = make_ctx(tmp_path)
    await index_text(ctx, "ratings.md", TABLE)

    async def quiet(*_):
        return None

    plan = await analyze(
        ctx, "what is the immediate corrective action for unacceptable performance?", [], quiet
    )
    plan.final_k = 1  # without the fix only the best-matching half would reach the model
    found = await retrieve(ctx, plan, [], quiet)
    texts = [found.chunks[c.chunk_id]["text"] for c in found.selected]
    assert any("Unacceptable" in t for t in texts) and any("Outstanding" in t for t in texts)
    assert [e for e in found.trace["selected"] if "completes" in e]


async def test_spelling_correction_leaves_real_words_alone(tmp_path):
    ctx = make_ctx(tmp_path)
    await index_text(ctx, "a.md", "The records are kept for audit purposes by Mira.\n")
    assert correct_spelling(ctx.store, "what are the rewards?") == ("what are the rewards?", {})
    fixed, applied = correct_spelling(ctx.store, "what are the recrods?")
    assert fixed == "what are the records?" and applied == {"recrods": "records"}


def test_the_prompt_forbids_document_names_and_false_missing_claims():
    assert "According to <document name>" not in INSTRUCTION
    assert "Never write document names" in INSTRUCTION
    assert "never claim that something is missing" in INSTRUCTION
    assert "According to" not in INSTRUCTION.split("Example output:")[1]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("According to Keka PMS.pdf, the hike is 0%.", "The hike is 0%."),
        ("It is 2%. According to Keka PMS.pdf, it is 5%.", "It is 2%. It is 5%."),
        ("It is up to 5% (Keka PMS.pdf).", "It is up to 5%."),
        (
            "The 2% applies, according to the Keka PMS.pdf document, to everyone.",
            "The 2% applies, to everyone.",
        ),
        ("Based on technology.md the system uses satellites.", "The system uses satellites."),
        ("The plan is based on technology and data.", "The plan is based on technology and data."),
    ],
)
def test_document_names_are_stripped_from_answers_but_ordinary_words_are_not(answer, expected):
    assert strip_document_names(answer, ["Keka PMS.pdf", "technology.md"]) == expected


class NamesTheDocument(FakeModel):
    def answer(self, prompt):
        return {**super().answer(prompt), "answer": "According to a.txt, Mira leads Acme."}


def test_an_answer_that_names_the_document_is_published_without_the_name(tmp_path):
    with client(tmp_path, NamesTheDocument()) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        assert reply["answer"] == "Mira leads Acme." and reply["citations"]
