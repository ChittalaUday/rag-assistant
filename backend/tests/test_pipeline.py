import pytest
from fakes import FakeModel
from helpers import index_text, make_ctx
from knowledge_engine.ingestion import intake
from knowledge_engine.pipeline.indexing import PIPELINE_VERSION, process


class Situating(FakeModel):
    """Answers the contextual-summary prompt; counts how often it was asked."""

    def __init__(self, reply=None):
        self.summaries = 0
        self.reply = reply or {"context": "This passage names who leads the Acme team."}

    async def generate(self, prompt, schema, max_tokens=None, think=False, model=None):
        if "context" in schema.get("properties", {}):
            self.summaries += 1
            return self.reply
        return await super().generate(prompt, schema)


@pytest.fixture
def ctx(tmp_path):
    ctx = make_ctx(tmp_path, Situating())
    yield ctx
    ctx.store.close()


async def test_indexing_builds_searchable_chunks_with_offsets(ctx):
    id = await index_text(ctx, "team.md", "# Team\n\nMira leads Acme. Acme builds things.\n")
    d = ctx.store.documents.get(id)
    assert d["status"] == "ready" and d["chunk_count"] == 1
    assert d["pipeline_version"] == PIPELINE_VERSION
    (chunk,) = ctx.store.chunks.for_document(id)
    assert chunk["heading"] == "Team" and chunk["char_start"] == 0
    text = ctx.store.documents.text(id)
    assert text[chunk["char_start"] : chunk["char_end"]] == chunk["text"]
    (row,) = ctx.store.chunks.get_many([chunk["id"]])
    assert row["context"] == "team › Team"  # no summaries unless enabled
    assert ctx.model.client.summaries == 0


async def test_contextual_summaries_join_the_search_context_and_are_cached(tmp_path):
    ctx = make_ctx(tmp_path, Situating(), contextual_chunks=True, context_model="small:1b")
    id = await index_text(ctx, "team.md", "# Team\n\nMira leads Acme.\n")
    (chunk,) = ctx.store.chunks.get_many(ctx.store.chunks.ids(id))
    assert chunk["context"] == "team › Team\nThis passage names who leads the Acme team."
    assert "This passage" not in chunk["text"]  # the evidence itself is untouched
    calls = ctx.model.client.summaries
    ctx.store.documents.requeue(id)
    await process(ctx, id)  # re-indexing unchanged text reuses the cached summary
    assert ctx.model.client.summaries == calls == 1
    ctx.store.close()


async def test_a_failed_summary_keeps_the_passage_searchable_with_a_warning(tmp_path):
    class Broken(Situating):
        async def generate(self, prompt, schema, max_tokens=None, think=False, model=None):
            if "context" in schema.get("properties", {}):
                raise ValueError("model output was not JSON")
            return await super().generate(prompt, schema)

    ctx = make_ctx(tmp_path, Broken(), contextual_chunks=True)
    id = await index_text(ctx, "team.md", "Mira leads Acme.")
    d = ctx.store.documents.get(id)
    assert d["status"] == "ready" and any("could not be summarised" in w for w in d["warnings"])
    ctx.store.close()


async def test_reindexing_is_idempotent(ctx):
    id = await index_text(ctx, "team.md", "Mira leads Acme.")
    before = [r["text"] for r in ctx.store.chunks.for_document(id)]
    ctx.store.documents.requeue(id)
    await process(ctx, id)
    assert [r["text"] for r in ctx.store.chunks.for_document(id)] == before


async def test_deleting_a_document_removes_its_chunks_and_vocabulary(ctx):
    a = await index_text(ctx, "a.md", "Mira leads Acme.")
    b = await index_text(ctx, "b.md", "Zephyrine audits payroll.")
    intake.delete_document(ctx, b)
    assert ctx.store.chunks.ids(b) == [] and ctx.store.chunks.ids(a)
    assert "zephyrine" not in ctx.store.chunks.known_words(["zephyrine"])


async def test_replacing_a_document_swaps_its_chunks_atomically(ctx):
    id = await index_text(ctx, "a.md", "Mira leads Acme.")
    old_chunk = ctx.store.chunks.ids(id)
    path = ctx.settings.data_dir / "replacement.md"
    path.write_text("Nothing about anyone here, just some plain sentences about weather.")
    ctx.store.documents.stage_replacement(id, "replacement.md", "h" * 64, str(path))
    await process(ctx, id)
    assert ctx.store.documents.get(id)["name"] == "replacement.md"
    assert ctx.store.chunks.ids(id) != old_chunk


async def test_wrong_embedding_size_fails_the_document_clearly(tmp_path):
    class Wide(FakeModel):
        async def embed(self, texts):
            return [[0.1] * 8 for _ in texts]

    ctx = make_ctx(tmp_path, Wide())
    id = await index_text(ctx, "a.md", "Mira leads Acme.")
    d = ctx.store.documents.get(id)
    assert d["status"] == "failed" and "384 dimensions" in d["error"]
    ctx.store.close()
