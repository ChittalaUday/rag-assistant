"""Regression tests for the findings of the independent engine review (UDA-61)."""

import configparser
import time
from pathlib import Path

import pytest
from fakes import FakeModel, ready
from helpers import index_text, make_ctx, relations
from knowledge_engine.config import Settings
from knowledge_engine.ingestion.chunker import chunk_document
from knowledge_engine.pipeline.indexing import clean_uploads
from knowledge_engine.retrieval.analyzer import analyze, correct_spelling, link_entities
from knowledge_engine.store.chunks import ChunkRepo
from knowledge_engine.text import clean_labels, normalize_text
from test_api import ConflictModel, client, upload

# -- excluded labels must hide relations as well as passages -----------------------------------


async def test_relations_supported_only_by_excluded_documents_do_not_exist_for_that_query(tmp_path):
    ctx = make_ctx(tmp_path, ConflictModel())
    await index_text(ctx, "a.txt", "Mira reports to Anil.")
    await index_text(ctx, "b.txt", "Mira reports to Bob.", labels=["synthetic"])
    graph = ctx.store.graph
    assert len(graph.relations()) == 2
    (visible,) = graph.relations(exclude_labels=["synthetic"])
    assert visible["object_name"] == "Anil" and visible["evidence_count"] == 1
    ids = [r["id"] for r in graph.relations()]
    assert {e["quote"] for e in graph.evidence(ids)} == {
        "Mira reports to Anil.",
        "Mira reports to Bob.",
    }
    assert {e["quote"] for e in graph.evidence(ids, exclude_labels=["synthetic"])} == {
        "Mira reports to Anil."
    }


def test_excluding_synthetic_documents_removes_their_conflicts_too(tmp_path):
    with client(tmp_path, ConflictModel()) as c:
        ready(c, upload(c, "a.txt", "Mira reports to Anil.")["id"])
        ready(c, upload(c, "b.txt", "Mira reports to Bob.", labels="synthetic")["id"])
        question = {"question": "Who does Mira report to?"}
        assert c.post("/api/chat", json=question).json()["status"] == "conflicting_evidence"
        for spelling in ("synthetic", "Synthetic "):  # labels are matched the way they are stored
            reply = c.post("/api/chat", json={**question, "exclude_labels": [spelling]}).json()
            assert reply["status"] != "conflicting_evidence", spelling
            assert reply["plan"]["conflicts"] == [], spelling


def test_label_cleaning_is_shared_by_upload_search_and_cli():
    assert clean_labels([" Synthetic ", "a b", "synthetic", ""]) == ["ab", "synthetic"]


# -- uploads are never deleted because of how their path is spelled ----------------------------


async def test_cleanup_matches_uploads_by_hash_name_not_path_spelling(tmp_path):
    ctx = make_ctx(tmp_path)
    id = await index_text(ctx, "a.txt", "Mira leads Acme.")
    stored = Path(ctx.store.documents.path(id))
    assert stored.exists()
    orphan = tmp_path / ("f" * 64 + ".txt")
    orphan.write_text("nobody references me")
    # the same file reached from another working directory or through a symlinked directory
    ctx.store.db.run("UPDATE documents SET path=%s", (f"data/{stored.name}",))
    clean_uploads(ctx)
    assert stored.exists() and not orphan.exists()
    assert ctx.upload_path(f"data/{stored.name}") == stored  # reads find it too


# -- cross-site pages cannot write to the local API --------------------------------------------


def test_cross_site_writes_and_foreign_hosts_are_refused(tmp_path):
    with client(tmp_path) as c:
        evil = {"Origin": "https://evil.example"}
        assert c.post("/api/documents/reindex", headers=evil).status_code == 403
        assert (
            c.post(
                "/api/documents", files={"file": ("a.txt", b"Mira leads Acme.")}, headers=evil
            ).status_code
            == 403
        )
        assert c.get("/api/documents").json()["documents"] == []
        assert c.post("/api/documents/reindex", headers={"Origin": "null"}).status_code == 403
        # the UI's own origin and tools that send no Origin keep working
        assert (
            c.post(
                "/api/documents/reindex", headers={"Origin": "http://127.0.0.1:5173"}
            ).status_code
            == 202
        )
        assert c.post("/api/documents/reindex").status_code == 202
        # DNS rebinding: a foreign name pointing at 127.0.0.1 is not this server's name
        assert c.get("/api/health", headers={"Host": "evil.example"}).status_code == 400


# -- hostile text is a client error, not a crash -----------------------------------------------


def test_nul_bytes_and_lone_surrogates_are_rejected_with_422(tmp_path):
    with client(tmp_path) as c:
        assert c.post("/api/chat", json={"question": "hi\x00there"}).status_code == 422
        assert c.get("/api/entities", params={"query": "a\x00b"}).status_code == 422
        assert c.get("/api/sources/a%00b").status_code == 422
        reply = c.post("/api/chat", json={"question": "hi", "exclude_labels": ["a\x00b"]})
        assert reply.status_code != 503  # not mislabelled as a model failure


# -- operations -------------------------------------------------------------------------------


def test_health_warns_about_evidence_from_another_extractor_and_not_about_uploads_in_flight(
    tmp_path,
):
    with client(tmp_path) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        db = c.app.state.ctx.store.db
        assert c.get("/api/health").json()["warnings"] == []
        db.run(
            "INSERT INTO documents(id,name,content_hash,status,path) VALUES('q','q.txt','h','queued','x')"
        )
        index = c.get("/api/health").json()["index"]
        assert (
            index["stale_documents"] == 0
        )  # an upload being processed is not "indexed by an older pipeline"
        db.run("UPDATE relation_evidence SET extractor='old-model|abc|p1'")
        warnings = c.get("/api/health").json()["warnings"]
        assert any("different model, ontology or prompt" in w for w in warnings)


def test_a_predicate_missing_from_the_current_ontology_does_not_break_chat(tmp_path):
    with client(tmp_path) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        c.app.state.ctx.store.db.run("UPDATE relations SET predicate='mentors'")
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"})
        assert reply.status_code == 200


def test_alembic_ini_points_at_the_migrations_directory():
    ini = configparser.ConfigParser(interpolation=None)
    root = Path(__file__).resolve().parents[1]
    ini.read(root / "alembic.ini")
    location = ini["alembic"]["script_location"].replace("%(here)s", str(root))
    assert (Path(location) / "env.py").exists()


# -- answers -----------------------------------------------------------------------------------


class BlankAnswerModel(FakeModel):
    def answer(self, prompt):
        return {**super().answer(prompt), "answer": "  "}


class SubstringAnswerModel(FakeModel):
    def answer(self, prompt):
        return {**super().answer(prompt), "answer": "Mirage leads Acmeville."}


def test_a_blank_answer_is_not_published_as_answered(tmp_path):
    with client(tmp_path, BlankAnswerModel()) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        assert reply["status"] == "insufficient_evidence" and reply["warnings"]
        assert reply["citations"] == []


def test_entity_names_inside_other_words_do_not_highlight_relations(tmp_path):
    with client(tmp_path, SubstringAnswerModel()) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        assert reply["citations"] and reply["graph"]["highlighted_edge_ids"] == []


def test_a_self_contained_follow_up_is_not_polluted_by_the_previous_question(tmp_path):
    with client(tmp_path) as c:  # FakeModel returns the follow-up unchanged
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        first = c.post("/api/chat", json={"question": "What does Acme build?"}).json()
        second = c.post(
            "/api/chat",
            json={"question": "Who leads Acme?", "conversation_id": first["conversation_id"]},
        ).json()
        assert second["plan"]["search_text"] == "Who leads Acme?"


# -- query understanding -----------------------------------------------------------------------


class TeamModel(FakeModel):
    def extract(self, prompt):
        return {
            "entities": [
                {"name": "Maria", "type": "Person"},
                {"name": "Arun Ballah", "type": "Person"},
            ],
            "relations": [
                {
                    "subject": "Arun Ballah",
                    "predicate": "reports_to",
                    "object": "Maria",
                    "phrase": "reports to",
                    "quote": "Arun Ballah reports to Maria.",
                }
            ],
        }


@pytest.fixture
async def ctx(tmp_path):
    ctx = make_ctx(tmp_path, TeamModel())
    await index_text(ctx, "team.md", "Maria leads Nova. Arun Ballah reports to Maria.\n")
    await index_text(ctx, "policy.md", "Expenses are submitted there after approval.\n")
    return ctx


async def test_names_and_function_words_are_not_spelling_corrected(ctx):
    for question in ("Marta leads which team?", "Where do I submit expenses?"):
        assert correct_spelling(ctx.store, question) == (question, {})


async def test_typo_tolerant_linking_also_works_in_long_questions(ctx):
    short = link_entities(ctx.store.graph, ctx.ontology, "Who does Arun Balla report to?")
    long = link_entities(
        ctx.store.graph, ctx.ontology, "Who does Arun Balla report to in the company today?"
    )
    assert [e.name for e in short] == [e.name for e in long] == ["Arun Ballah"]


async def test_follow_up_that_the_model_returns_unchanged_stays_unchanged(ctx):
    async def quiet(*_):
        return None

    plan = await analyze(
        ctx, "Who leads Nova?", [{"role": "user", "content": "What does Acme build?"}], quiet
    )
    assert plan.search_text == "Who leads Nova?"


# -- chunking and normalising cannot be made to hang -------------------------------------------


def test_non_positive_chunk_sizes_are_rejected_instead_of_looping():
    with pytest.raises(ValueError, match="max_chars"):
        chunk_document("some text", max_chars=0)
    with pytest.raises(ValueError):
        Settings(chunk_chars=0)


def within(seconds: float, work) -> None:
    start = time.perf_counter()
    work()
    assert time.perf_counter() - start < seconds


def test_pathological_uploads_are_processed_in_linear_time():
    within(1.0, lambda: chunk_document("# x" + " " * 20000 + "y"))
    within(2.0, lambda: normalize_text("<!--" * 40000))
    table = "| a | b |\n|---|---|\n" + "| 1 | 2 |\n" * 20000
    within(3.0, lambda: chunk_document(table))
    assert normalize_text("a <!-- gone --> b <!-- open") == "a  b <!-- open"
    chunks = chunk_document("# T\n\n" + "| a | b |\n|---|---|\n" + "| 1 | 2 |\n" * 400)
    assert all(c.table_header == "| a | b |" for c in chunks[1:])


# -- retrieval ranking -------------------------------------------------------------------------


def test_dense_neighbours_come_back_best_first_even_when_the_index_returns_them_loosely():
    class Db:
        def rows(self, *_):
            return [{"id": "b", "score": 0.5}, {"id": "a", "score": 0.9}, {"id": "c", "score": 0.7}]

    assert ChunkRepo(Db()).dense([0.0] * 384, 3, []) == [("a", 0.9), ("c", 0.7), ("b", 0.5)]


async def test_overlapping_chunks_do_not_count_one_sentence_twice(tmp_path):
    words = [
        "alpha",
        "bravo",
        "charlie",
        "delta",
        "echo",
        "foxtrot",
        "golf",
        "hotel",
        "india",
        "juliet",
        "kilo",
        "lima",
    ]

    def filler(start: int, count: int) -> str:
        return " ".join(
            f"Note {i} concerns {words[i % 12]} and {words[(i * 5) % 12]} in sector {i * 13}."
            for i in range(start, start + count)
        )

    for lead in range(4, 16):  # find a layout where the sentence sits in the chunk overlap
        text = filler(0, lead) + " Mira leads Acme. " + filler(100, 12)
        if sum("Mira leads Acme." in c.text for c in chunk_document(text)) == 2:
            break
    else:
        pytest.fail("no overlapping layout found")
    ctx = make_ctx(tmp_path)
    await index_text(ctx, "a.txt", text)
    (relation,) = relations(ctx)
    assert relation["evidence_count"] == 1 and relation["confidence"] < 0.96
