import pytest
from fakes import FakeModel, hash_embedding
from helpers import index_text, make_ctx
from knowledge_engine.ingestion import intake
from knowledge_engine.retrieval.analyzer import (
    analyze,
    classify,
    correct_spelling,
    needs_context,
)
from knowledge_engine.retrieval.models import QueryPlan
from knowledge_engine.retrieval.search import retrieve


class Spy(FakeModel):
    def __init__(self):
        self.prompts = []

    async def generate(self, prompt, schema, max_tokens=None, think=False, model=None):
        self.prompts.append((prompt, schema))
        if "question" in schema.get("properties", {}):
            return {"question": "Who leads Acme?"}
        return await super().generate(prompt, schema)


@pytest.fixture
async def ctx(tmp_path):
    ctx = make_ctx(tmp_path, Spy())
    await index_text(
        ctx, "roster.md", "# Roster\n\nMira leads Acme. Acme builds payroll software for banks.\n"
    )
    await index_text(
        ctx,
        "holidays.md",
        "# Holidays\n\nThe holiday calendar lists public holidays and floating leave days.\n",
    )
    await index_text(
        ctx,
        "tax.md",
        "# Tax\n\nPayroll tax filing happens quarterly for every region.\n",
        labels=["synthetic"],
    )
    yield ctx
    ctx.store.close()


async def quiet(stage, message):
    return None


def plan_for(question, terms=None):
    from knowledge_engine.text import ordered_terms

    return QueryPlan(question, question, False, terms or ordered_terms(question), "lookup", 5)


def doc_of(ctx, chunk_id):
    return ctx.store.chunks.get_many([chunk_id])[0]["document_name"]


async def test_dense_search_uses_vector_similarity(ctx):
    hits = ctx.store.chunks.dense(hash_embedding("payroll tax filing quarterly"), 3, [])
    assert doc_of(ctx, hits[0][0]) == "tax.md" and hits[0][1] > hits[-1][1]
    assert len(hits) == 3


async def test_sparse_search_matches_any_term_and_ranks_denser_matches_first(ctx):
    hits = ctx.store.chunks.sparse(["payroll", "tax", "holiday"], 10, [])
    docs = [doc_of(ctx, c) for c, _ in hits]
    assert docs[0] in {"tax.md", "holidays.md"} and set(docs) == {
        "tax.md",
        "holidays.md",
        "roster.md",
    }
    assert ctx.store.chunks.sparse(["zzzunknown"], 10, []) == []
    assert ctx.store.chunks.sparse([], 10, []) == []
    # headings are indexed too: "roster" appears only in the document title/heading
    assert [doc_of(ctx, c) for c, _ in ctx.store.chunks.sparse(["roster"], 5, [])] == ["roster.md"]


async def test_label_exclusion_applies_to_both_retrievers(ctx):
    vector = hash_embedding("payroll tax filing")
    assert "tax.md" in {doc_of(ctx, c) for c, _ in ctx.store.chunks.dense(vector, 10, [])}
    assert "tax.md" not in {
        doc_of(ctx, c) for c, _ in ctx.store.chunks.dense(vector, 10, ["synthetic"])
    }
    assert "tax.md" not in {
        doc_of(ctx, c) for c, _ in ctx.store.chunks.sparse(["payroll", "tax"], 10, ["synthetic"])
    }


async def test_hybrid_retrieval_ranks_the_answering_document_first_and_traces_why(ctx):
    plan = await analyze(ctx, "Who leads Acme?", [], quiet)
    result = await retrieve(ctx, plan, [], quiet)
    top = result.selected[0]
    assert doc_of(ctx, top.chunk_id) == "roster.md"
    assert {"dense", "sparse"} <= set(top.ranks)
    assert (
        result.trace["candidates"] >= 1 and result.trace["selected"][0]["document"] == "roster.md"
    )
    excluded = await retrieve(ctx, plan_for("payroll tax filing"), ["synthetic"], quiet)
    assert all(doc_of(ctx, c.chunk_id) != "tax.md" for c in excluded.selected)


async def test_stopword_only_questions_still_retrieve_by_meaning(ctx):
    plan = await analyze(ctx, "what is the", [], quiet)
    assert plan.terms == []
    result = await retrieve(ctx, plan, [], quiet)
    assert result.selected  # dense search needs no keywords


async def test_empty_index_retrieves_nothing_without_calling_a_model(tmp_path):
    ctx = make_ctx(tmp_path, Spy())
    assert (await retrieve(ctx, plan_for("anything"), [], quiet)).selected == []
    assert ctx.model.client.prompts == []
    ctx.store.close()


def test_question_classification_and_follow_up_detection():
    assert classify("List all policies") == "overview"
    assert classify("Who reports to Acme?") == "relationship"
    assert classify("What is the rating scale?") == "lookup"
    assert needs_context("What about her?") and needs_context("and the second one")
    assert not needs_context("What is the leave policy for part-time employees in Hyderabad?")


async def test_follow_up_is_rewritten_with_one_small_model_call(ctx):
    history = [
        {"role": "user", "content": "Who leads Acme?"},
        {"role": "assistant", "content": "Mira."},
    ]
    plan = await analyze(ctx, "What does she lead?", history, quiet)
    assert plan.rewritten and plan.search_text == "Who leads Acme?"
    prompt, schema = ctx.model.client.prompts[-1]
    assert "FINAL QUESTION: What does she lead?" in prompt and "user: Who leads Acme?" in prompt
    assert schema["properties"]["question"]["maxLength"] == 400
    ctx.settings.rewrite_followups = False
    plan = await analyze(ctx, "What does she lead?", history, quiet)
    assert not plan.rewritten and "Who leads Acme?" in plan.search_text  # lexical fallback


async def test_misspelt_words_are_corrected_from_the_corpus_vocabulary(ctx):
    text, fixes = correct_spelling(ctx.store, "what is the paroll taxx filing calendra?")
    assert fixes == {
        "paroll": "payroll",
        "taxx": "tax",
        "calendra": "calendar",
    }  # incl. a transposition
    assert text == "what is the payroll tax filing calendar?"
    assert correct_spelling(ctx.store, "Who is Kukunuri at Acme in 2026? It's fine.") == (
        "Who is Kukunuri at Acme in 2026? It's fine.",
        {},
    )  # names, digits, apostrophes, known words
    text, fixes = correct_spelling(ctx.store, "list the holi;days")
    assert text == "list the holidays" and fixes == {"holi;days": "holidays"}
    assert correct_spelling(ctx.store, "zzzzqqqq") == ("zzzzqqqq", {})  # nothing close: left alone


async def test_corrections_drive_retrieval_and_are_reported_in_the_plan(ctx):
    plan = await analyze(ctx, "paroll taxx filing", [], quiet)
    assert plan.corrections == {"paroll": "payroll", "taxx": "tax"}
    assert plan.search_text == "payroll tax filing" and plan.question == "paroll taxx filing"
    hits = ctx.store.chunks.sparse(plan.terms, 3, [])
    assert doc_of(ctx, hits[0][0]) == "tax.md"


async def test_vocabulary_follows_the_corpus_when_documents_change(ctx):
    assert ctx.store.chunks.spelling(["quarterlyy"]) == {"quarterlyy": "quarterly"}
    tax = ctx.store.db.one("SELECT id FROM documents WHERE name='tax.md'")["id"]
    intake.delete_document(ctx, tax)
    assert ctx.store.chunks.spelling(["quarterlyy"]) == {}


async def test_dense_search_keeps_looking_when_filters_exclude_the_nearest_chunks(tmp_path):
    ctx = make_ctx(tmp_path, Spy())
    for i in range(30):  # many near-identical synthetic chunks crowd out the one real match
        await index_text(
            ctx,
            f"fake{i}.md",
            f"Payroll tax filing quarterly notes number {i}.",
            labels=["synthetic"],
        )
    await index_text(ctx, "real.md", "Payroll filing handled by finance staff.")
    hits = ctx.store.chunks.dense(hash_embedding("payroll tax filing quarterly"), 5, ["synthetic"])
    assert [doc_of(ctx, c) for c, _ in hits] == ["real.md"]
    ctx.store.close()


async def test_reranker_model_reorders_candidates_by_its_relevance(tmp_path):
    class Reranking(FakeModel):
        async def relevance(self, model, query, documents):
            assert model == "reranker:0.6b" and query == "Which days are holidays?"
            return [0.99 if "Tax" in d else 0.01 for d in documents]

    ctx = make_ctx(tmp_path, Reranking(), rerank_model="reranker:0.6b", rerank_weight=1.0)
    await index_text(ctx, "holidays.md", "# Holidays\n\nThe holiday calendar lists holidays.\n")
    await index_text(ctx, "tax.md", "# Tax\n\nPayroll tax filing happens quarterly.\n")
    result = await retrieve(ctx, plan_for("Which days are holidays?"), [], quiet)
    assert doc_of(ctx, result.selected[0].chunk_id) == "tax.md"
    assert result.selected[0].features["model"] == 0.99
    ctx.store.close()


def test_yes_probability_normalises_against_no_in_any_casing():
    import math

    from knowledge_engine.llm.ollama import yes_probability

    top = [
        {"token": "yes", "logprob": math.log(0.6)},
        {"token": " Yes", "logprob": math.log(0.2)},
        {"token": "no", "logprob": math.log(0.2)},
        {"token": "maybe", "logprob": math.log(0.5)},
    ]
    assert yes_probability(top) == pytest.approx(0.8)
    assert yes_probability([{"token": "maybe", "logprob": 0.0}]) == 0.0


async def test_second_hop_follows_a_name_to_another_document(tmp_path):
    from knowledge_engine.retrieval.hop import bridge_terms

    ctx = make_ctx(tmp_path, second_hop="always")
    await index_text(ctx, "log.md", "# Log\n\nFinance (Hana Park) signed off on the overrun.\n")
    await index_text(ctx, "people.md", "# People\n\nHana Park is the Head of People.\n")
    await index_text(ctx, "misc.md", "# Misc\n\nThe cafeteria opens at noon on weekdays.\n")
    plan = plan_for("Who signed off on the overrun?")
    plan.final_k = 1
    result = await retrieve(ctx, plan, [], quiet)
    docs = [doc_of(ctx, c.chunk_id) for c in result.selected]
    assert docs == ["log.md", "people.md"]
    assert result.selected[1].ranks == {"hop": 1}
    assert set(result.trace["bridge_terms"]) == {"hana", "park"}
    # words found in no other passage cannot bridge; question words are never bridges
    assert bridge_terms({"overrun"}, "Finance signed off the overrun", {"finance": 1}) == []
    # names win over common words; without names (uncased scripts) rarity decides
    df = {"lead": 3, "unavailable": 2, "asha": 4, "rao": 4}
    sentence = "Leela is the backup lead whenever Asha Rao is unavailable"
    assert bridge_terms({"leela"}, sentence, df) == ["asha", "rao"]
    assert bridge_terms(set(), "the backup lead is unavailable", df) == ["unavailable", "lead"]
    ctx.store.close()


async def test_second_hop_is_off_by_default(ctx):
    plan = plan_for("Who leads Acme?")
    plan.final_k = 1
    result = await retrieve(ctx, plan, [], quiet)
    assert len(result.selected) == 1 and "bridge_terms" not in result.trace


async def test_a_failing_reranker_falls_back_to_feature_ranking(tmp_path):
    import httpx

    class Broken(FakeModel):
        async def relevance(self, model, query, documents):
            raise httpx.HTTPStatusError("400", request=None, response=None)  # type: ignore[arg-type]

    ctx = make_ctx(tmp_path, Broken(), rerank_model="missing:0.6b")
    await index_text(ctx, "roster.md", "# Roster\n\nMira leads Acme.\n")
    result = await retrieve(ctx, plan_for("Who leads Acme?"), [], quiet)
    assert doc_of(ctx, result.selected[0].chunk_id) == "roster.md"
    assert result.trace["reranked"] is False
    ctx.store.close()
