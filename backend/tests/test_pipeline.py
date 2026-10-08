import pytest
from fakes import FakeModel
from helpers import index_text, make_ctx, relations
from knowledge_engine.extraction.extractor import PROMPT_VERSION
from knowledge_engine.ingestion import intake
from knowledge_engine.pipeline.indexing import PIPELINE_VERSION


class CountingModel(FakeModel):
    def __init__(self):
        self.extractions = 0

    async def generate(self, prompt, schema, max_tokens=None):
        if "relations" in schema.get("properties", {}):
            self.extractions += 1
        return await super().generate(prompt, schema)


@pytest.fixture
def ctx(tmp_path):
    ctx = make_ctx(tmp_path, CountingModel())
    yield ctx
    ctx.store.close()


async def test_indexing_builds_chunks_entities_relations_and_mentions(ctx):
    id = await index_text(ctx, "team.md", "# Team\n\nMira leads Acme. Acme builds things.\n")
    d = ctx.store.documents.get(id)
    assert (
        d["status"] == "ready"
        and d["chunk_count"] == 1
        and d["pipeline_version"] == PIPELINE_VERSION
    )
    (chunk,) = ctx.store.chunks.for_document(id)
    assert chunk["heading"] == "Team" and chunk["char_start"] == 0
    names = {n["label"]: n for n in ctx.store.graph.nodes()}
    assert set(names) == {"Mira", "Acme"} and names["Mira"]["mentions"] == 1
    (rel,) = relations(ctx)
    assert (rel["subject_name"], rel["predicate"], rel["object_name"]) == ("Mira", "leads", "Acme")
    (ev,) = ctx.store.graph.evidence([rel["id"]])
    assert ev["chunk_id"] == chunk["id"] and ev["quote"] == "Mira leads Acme."
    text = ctx.store.documents.text(id)
    assert text
    stored = ctx.store.db.one("SELECT quote_start, quote_end, extractor FROM relation_evidence")
    assert (
        text[
            stored["quote_start"] + chunk["char_start"] : stored["quote_end"] + chunk["char_start"]
        ]
        == ev["quote"]
    )
    assert (
        f"p{PROMPT_VERSION}" in stored["extractor"] and ctx.ontology.version in stored["extractor"]
    )


async def test_reindexing_is_idempotent_and_reuses_cached_extractions(ctx):
    id = await index_text(ctx, "team.md", "Mira leads Acme.")
    before = (sorted(n["id"] for n in ctx.store.graph.nodes()), [r["id"] for r in relations(ctx)])
    model_calls = ctx.model.client.extractions
    from knowledge_engine.pipeline.indexing import process

    ctx.store.documents.requeue(id)
    await process(ctx, id)
    after = (sorted(n["id"] for n in ctx.store.graph.nodes()), [r["id"] for r in relations(ctx)])
    assert before == after and ctx.store.graph.stats()["evidence"] == 1
    assert ctx.model.client.extractions == model_calls  # raw output came from the cache


async def test_same_fact_in_two_documents_is_one_relation_with_two_pieces_of_evidence(ctx):
    await index_text(ctx, "a.md", "Mira leads Acme.")
    one = relations(ctx)[0]["confidence"]
    await index_text(ctx, "b.md", "Mira leads Acme. Different filler words here.")
    (rel,) = relations(ctx)
    assert rel["evidence_count"] == 2 and rel["confidence"] > one
    assert ctx.store.graph.stats()["entities"] == 2


async def test_deleting_a_document_keeps_shared_entities_and_drops_orphans(ctx):
    a = await index_text(ctx, "a.md", "Mira leads Acme.")
    b = await index_text(ctx, "b.md", "Mira leads Acme. Second document.")
    intake.delete_document(ctx, a)
    assert ctx.store.graph.stats()["entities"] == 2 and len(relations(ctx)) == 1
    intake.delete_document(ctx, b)
    assert ctx.store.graph.stats() == {"entities": 0, "relations": 0, "evidence": 0}


async def test_replacing_a_document_swaps_its_evidence_atomically(ctx):
    id = await index_text(ctx, "a.md", "Mira leads Acme.")
    old_chunk = ctx.store.chunks.ids(id)
    from knowledge_engine.pipeline.indexing import process

    path = ctx.settings.data_dir / "replacement.md"
    path.write_text("Nothing about anyone here, just some plain sentences about weather.")
    ctx.store.documents.stage_replacement(id, "replacement.md", "h" * 64, str(path))
    await process(ctx, id)
    assert ctx.store.documents.get(id)["name"] == "replacement.md"
    assert ctx.store.chunks.ids(id) != old_chunk and relations(ctx) == []
    assert ctx.store.graph.stats()["entities"] == 0


async def test_low_information_chunks_never_reach_the_model(ctx):
    template = "| Rating | Self | Manager |\n" * 30
    await index_text(ctx, "form.md", template)
    assert ctx.model.client.extractions == 0
    assert ctx.store.chunks.count() >= 1  # still searchable as text


async def test_wrong_embedding_size_fails_the_document_clearly(tmp_path):
    class Wide(FakeModel):
        async def embed(self, texts):
            return [[0.1] * 8 for _ in texts]

    ctx = make_ctx(tmp_path, Wide())
    id = await index_text(ctx, "a.md", "Mira leads Acme.")
    d = ctx.store.documents.get(id)
    assert d["status"] == "failed" and "384 dimensions" in d["error"]
    ctx.store.close()


async def test_malformed_model_output_skips_the_passage_with_a_warning(tmp_path):
    class Garbled(FakeModel):
        def extract(self, prompt):
            return {"entities": "not a list"}

    ctx = make_ctx(tmp_path, Garbled())
    id = await index_text(ctx, "a.md", "Mira leads Acme.")
    d = ctx.store.documents.get(id)
    assert d["status"] == "ready" and any("could not be parsed" in w for w in d["warnings"])
    ctx.store.close()


async def test_node_mentions_add_up_across_chunks_even_when_counts_repeat(ctx):
    filler = " ".join(f"item{i}x" for i in range(150))  # distinct tokens: not a repetitive template
    id = await index_text(ctx, "long.md", f"Mira leads Acme. {filler}\n\nMira leads Acme.")
    assert len(ctx.store.chunks.ids(id)) >= 2
    nodes = {n["label"]: n for n in ctx.store.graph.nodes()}
    assert nodes["Mira"]["mentions"] == 2 and nodes["Acme"]["mentions"] == 2  # one per chunk


async def test_a_document_that_yields_no_entities_says_so(tmp_path):
    class Silent(FakeModel):
        def extract(self, prompt):  # finds facts only where there are some
            return (
                super().extract(prompt) if "Mira" in prompt else {"entities": [], "relations": []}
            )

    ctx = make_ctx(tmp_path, Silent())
    id = await index_text(
        ctx, "form.md", "Employee name: ____\nDesignation: ____\nReview period: ____"
    )
    warnings = ctx.store.documents.get(id)["warnings"]
    assert any("No entities or relationships were found" in w for w in warnings)
    id = await index_text(ctx, "ok.md", "Mira leads Acme.")
    assert not any("No entities" in w for w in ctx.store.documents.get(id)["warnings"])
