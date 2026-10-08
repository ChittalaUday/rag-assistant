import pytest
from fakes import FakeModel, hash_embedding
from helpers import index_text, make_ctx
from knowledge_engine.ingestion import intake
from knowledge_engine.retrieval.analyzer import (
    analyze,
    classify,
    correct_spelling,
    link_entities,
    needs_context,
)
from knowledge_engine.retrieval.graph import graph_candidates
from knowledge_engine.retrieval.models import QueryPlan
from knowledge_engine.retrieval.search import retrieve


class Spy(FakeModel):
    def __init__(self):
        self.prompts = []

    async def generate(self, prompt, schema, max_tokens=None):
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


def plan_for(question, terms=None, entities=()):
    from knowledge_engine.text import ordered_terms

    return QueryPlan(
        question, question, False, terms or ordered_terms(question), list(entities), "lookup", 5, 1
    )


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
    assert {"dense", "sparse"} <= set(top.ranks) and "graph" in top.ranks  # Acme is a linked entity
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


async def test_entities_link_by_name_alias_and_close_misspelling(ctx):
    class Deal(Spy):
        def extract(self, prompt):
            if "Northfield" in prompt.rsplit("PASSAGE:", 1)[1]:
                return {
                    "entities": [{"name": "Northfield Capital", "type": "Organization"}],
                    "relations": [],
                }
            return super().extract(prompt)

    ctx.model.client = Deal()
    await index_text(ctx, "deal.md", "Northfield Capital led the Series B for Acme.")
    ont = ctx.ontology
    names = lambda text: {e.name for e in link_entities(ctx.store.graph, ont, text)}  # noqa: E731
    assert "Acme" in names("Who leads Acme?") and "Mira" in names("tell me about mira")
    assert names("the weather today") == set()
    assert "Northfield Capital" in names("What did Northfield Capitol do?")  # one-letter typo


def test_question_classification_and_follow_up_detection():
    from knowledge_engine.retrieval.models import LinkedEntity

    e = LinkedEntity("e", "Acme", "Organization", "acme", 1)
    assert classify("List all policies", []) == "overview"
    assert classify("Who reports to Acme?", [e]) == "relationship"
    assert classify("What is the rating scale?", []) == "lookup"
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


def all_chunks(ctx):
    return {r["id"] for r in ctx.store.db.rows("SELECT id FROM chunks")}


async def test_graph_candidates_rank_chunks_by_entity_mentions_and_relation_evidence(ctx):
    plan = await analyze(ctx, "Who leads Acme?", [], quiet)
    assert plan.entities and plan.seeds == plan.entities  # a relationship-type question
    ranked, traversed = graph_candidates(ctx.store, plan, ctx.ontology, all_chunks(ctx))
    assert [doc_of(ctx, c) for c, _ in ranked] == ["roster.md"]
    assert [r["predicate"] for r in traversed] == ["leads"]
    assert graph_candidates(ctx.store, plan_for("nothing"), ctx.ontology, all_chunks(ctx))[0] == []


async def test_relations_that_do_not_bear_on_the_question_are_not_followed(ctx):
    plan = await analyze(
        ctx, "What colour is Acme?", [], quiet
    )  # Acme is linked, but 'leads' is off topic
    assert plan.entities
    ranked, traversed = graph_candidates(ctx.store, plan, ctx.ontology, set())
    assert traversed == [] and ranked == []
    plan = await analyze(
        ctx, "Who is the head of Acme?", [], quiet
    )  # 'head' matches leads' description
    assert [r["predicate"] for r in graph_candidates(ctx.store, plan, ctx.ontology, set())[1]] == [
        "leads"
    ]


async def test_graph_steers_retrieval_only_when_the_setting_and_question_type_allow(ctx):
    question = "Who leads Acme?"
    assert (await analyze(ctx, question, [], quiet)).seeds  # default: relationship questions
    assert not (await analyze(ctx, "What does Acme build?", [], quiet)).seeds  # a plain lookup
    ctx.settings.graph_retrieval = "off"
    plan = await analyze(ctx, question, [], quiet)
    assert plan.entities and plan.seeds == []  # still named, no longer steering
    ctx.settings.graph_retrieval = "always"
    assert (await analyze(ctx, "What does Acme build?", [], quiet)).seeds


async def test_mention_chunks_only_reinforce_what_text_search_found(ctx):
    plan = await analyze(ctx, "Who leads Acme?", [], quiet)
    roster = next(
        iter(
            all_chunks(ctx)
            & {
                r["id"]
                for r in ctx.store.db.rows(
                    "SELECT c.id FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.name='roster.md'"
                )
            }
        )
    )
    # relation evidence still brings the roster chunk in; without a text pool no extra chunk appears
    ranked, _ = graph_candidates(ctx.store, plan, ctx.ontology, set())
    assert [c for c, _ in ranked] == [roster]
    boosted, _ = graph_candidates(ctx.store, plan, ctx.ontology, {roster})
    assert boosted[0][1] > ranked[0][1]  # also found by search: reinforced


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
