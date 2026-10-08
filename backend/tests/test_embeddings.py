"""Changing the embedding model or its vector size: the column follows and documents are re-embedded."""

import pytest
from fakes import FakeModel, hash_embedding, ready
from helpers import index_text, make_ctx
from knowledge_engine.store.migrate import align_embedding_dimensions
from test_api import client, upload


async def test_a_different_vector_size_replaces_the_column_and_keeps_the_text(tmp_path):
    ctx = make_ctx(tmp_path)
    await index_text(ctx, "a.txt", "Mira leads Acme.")
    url, docs, chunks = ctx.settings.database_url, ctx.store.documents, ctx.store.chunks
    assert not align_embedding_dimensions(url, 384)  # same size: nothing to do
    assert align_embedding_dimensions(url, 768)
    assert not align_embedding_dimensions(url, 768)
    assert chunks.texts(chunks.db.rows("SELECT document_id FROM chunks")[0]["document_id"]) == [
        "Mira leads Acme."
    ]
    assert chunks.db.one("SELECT embedding FROM chunks")["embedding"] is None
    assert chunks.dense([0.1] * 768, 5, []) == []  # no vectors yet: not an error
    assert chunks.vectors([r["id"] for r in chunks.db.rows("SELECT id FROM chunks")]) == {}
    assert docs.reembedding_needed("all-minilm:l6-v2") == []
    assert docs.reembedding_needed("another-model") != []


def test_documents_embedded_by_another_model_are_outdated_and_rebuilt_at_start(tmp_path):
    with client(tmp_path) as c:
        first = upload(c, "a.txt", "Mira leads Acme.")["id"]
        ready(c, first)
        assert c.get("/api/health").json()["index"]["stale_documents"] == 0
    with client(tmp_path, embedding_model="other-model") as c:
        assert ready(c, first)["status"] == "ready"  # re-embedded without being asked
        assert c.app.state.ctx.store.chunks.embedding_models() == {"other-model": 1}
        assert c.get("/api/health").json()["index"]["stale_documents"] == 0


class Wide(FakeModel):
    async def embed(self, texts):
        return [hash_embedding(t) * 2 for t in texts]  # 768 dimensions


@pytest.mark.filterwarnings("ignore")
def test_switching_to_a_wider_model_rebuilds_vectors_and_search_still_works(tmp_path):
    with client(tmp_path) as c:
        first = upload(c, "a.txt", "Mira leads Acme.")["id"]
        ready(c, first)
    wide = {"embedding_model": "wide", "embedding_dimensions": 768}
    with client(tmp_path, Wide(), **wide) as c:
        assert ready(c, first)["status"] == "ready"
        hits = c.post("/api/search", json={"query": "Who leads Acme?"}).json()["hits"]
        assert hits and hits[0]["ranks"].get("dense") == 1
        assert c.app.state.ctx.store.chunks.embedding_models() == {"wide": 1}
