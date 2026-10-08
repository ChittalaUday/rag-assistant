import json
import re

from fakes import FakeModel, ready
from fastapi.testclient import TestClient
from knowledge_engine.app import create_app
from knowledge_engine.config import Settings
from knowledge_engine.pipeline.indexing import PIPELINE_VERSION
from knowledge_engine.store.chunks import ChunkRepo


def client(tmp_path, model=None, **settings):
    return TestClient(create_app(Settings(data_dir=tmp_path, **settings), model or FakeModel()))


def upload(c, name, text, **form):
    data = text if isinstance(text, bytes) else text.encode()
    return c.post("/api/documents", files={"file": (name, data)}, data=form).json()


def test_document_lifecycle(tmp_path):
    with client(tmp_path) as c:
        doc = upload(c, "a.txt", "Mira leads Acme.")
        assert ready(c, doc["id"])["status"] == "ready"
        assert upload(c, "b.txt", "Mira leads Acme.")["id"] == doc["id"]
        graph = c.get("/api/graph").json()
        assert len(graph["edges"]) == 1 and len(graph["nodes"]) == 2
        edge = graph["edges"][0]
        assert (edge["label"], edge["predicate"]) == ("leads", "leads")
        assert (
            edge["evidence"][0]["quote"] == "Mira leads Acme." and 0.5 < edge["confidence"] <= 0.95
        )
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        assert reply["citations"] and reply["status"] == "answered"
        follow = c.post(
            "/api/chat",
            json={"question": "Where does she work?", "conversation_id": reply["conversation_id"]},
        )
        assert follow.status_code == 200
        c.delete("/api/documents/" + doc["id"])
        assert c.get("/api/graph").json() == {
            "nodes": [],
            "edges": [],
            "highlighted_node_ids": [],
            "highlighted_edge_ids": [],
        }


def test_invalid_quotes_are_rejected_with_a_warning(tmp_path):
    with client(tmp_path) as c:
        d = upload(c, "a.txt", "Acme employs Mira.")
        assert ready(c, d["id"])["warnings"]
        assert c.get("/api/graph").json()["edges"] == []


class OfflineModel(FakeModel):
    async def embed(self, texts):
        raise RuntimeError("Ollama is offline")


def test_failed_replacement_keeps_evidence(tmp_path):
    with client(tmp_path) as c:
        d = upload(c, "a.txt", "Mira leads Acme.")
        ready(c, d["id"])
        c.put("/api/documents/" + d["id"], files={"file": ("bad.txt", b"\xff")})
        failed = ready(c, d["id"])
        assert failed["status"] == "failed"
        assert failed["name"] == d["name"] and failed["content_hash"] == d["content_hash"]
        edges = c.get("/api/graph").json()["edges"]
        assert len(edges) == 1 and edges[0]["evidence"][0]["document_name"] == d["name"]
        c.put("/api/documents/" + d["id"], files={"file": ("new.txt", b"Acme employs Mira.")})
        assert ready(c, d["id"])["status"] == "ready"
        assert c.get("/api/graph").json()["edges"] == []


def test_offline_job_and_stream_error(tmp_path):
    with client(tmp_path, OfflineModel()) as c:
        d = upload(c, "a.txt", "Mira leads Acme.")
        assert "offline" in ready(c, d["id"])["error"]
        # No evidence should yield an honest abstention without inventing a model result.
        response = c.post("/api/chat/stream", json={"question": "Who leads Acme?"})
        assert "event: status" in response.text and "event: result" in response.text
        assert "could not find supporting evidence" in response.text
        assert '"status": "insufficient_evidence"' in response.text


def test_upload_limits_and_sources(tmp_path):
    with client(tmp_path, max_upload_bytes=20) as c:
        assert c.post("/api/documents", files={"file": ("a.txt", b"x" * 21)}).status_code == 413
        assert c.post("/api/documents", files={"file": ("a.exe", b"x")}).status_code == 400
        d = upload(c, "a.txt", "Mira leads Acme.")
        ready(c, d["id"])
        citation = c.get("/api/graph").json()["edges"][0]["evidence"][0]
        source = c.get("/api/sources/" + citation["chunk_id"]).json()
        assert source["text"] == "Mira leads Acme." and source["document_name"] == "a.txt"
        assert c.get("/api/sources/nope").status_code == 404
        assert (
            c.post("/api/chat", json={"question": "hi", "conversation_id": "missing"}).status_code
            == 404
        )


class RecordingModel(FakeModel):
    def __init__(self):
        self.prompts = []
        self.calls = 0
        self.offline = False

    async def embed(self, texts):
        self.calls += 1
        if self.offline:
            raise RuntimeError("Ollama is offline")
        return await super().embed(texts)

    async def generate(self, prompt, schema, max_tokens=None):
        self.prompts.append(prompt)
        return await super().generate(prompt, schema)

    def answer_prompts(self):
        return [p for p in self.prompts if "FINAL QUESTION" not in p and "QUESTION:" in p]


def test_persisted_followups_dedup_and_chat_failure(tmp_path):
    model = RecordingModel()
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings, model)) as c:
        d = upload(c, "a.txt", "Mira leads Acme.")
        ready(c, d["id"])
        calls = model.calls
        upload(c, "copy.txt", "Mira leads Acme.")
        assert model.calls == calls  # duplicate content is never re-embedded
        cid = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()["conversation_id"]
    with TestClient(create_app(settings, model)) as c:  # survives a restart
        reply = c.post(
            "/api/chat", json={"question": "What does she lead?", "conversation_id": cid}
        )
        assert reply.status_code == 200
        assert "Who leads Acme?" in model.answer_prompts()[-1]  # history reaches the answer prompt
        assert len(c.get("/api/conversations/" + cid).json()["messages"]) == 4
        model.offline = True
        assert c.post("/api/chat", json={"question": "Acme?"}).status_code == 503
        stream = c.post("/api/chat/stream", json={"question": "Acme?"}).text
        assert "event: error" in stream and "offline" in stream


class SelectiveModel(FakeModel):
    def extract(self, prompt):
        return {
            "entities": [
                {"name": "Mira", "type": "Person"},
                {"name": "Acme", "type": "Organization"},
                {"name": "Nova", "type": "Product"},
            ],
            "relations": [
                {
                    "subject": "Mira",
                    "predicate": "leads",
                    "object": "Acme",
                    "phrase": "leads",
                    "quote": "Mira leads Acme.",
                },
                {
                    "subject": "Acme",
                    "predicate": "uses",
                    "object": "Nova",
                    "phrase": "uses",
                    "quote": "Acme uses Nova.",
                },
            ],
        }


def test_only_relations_the_answer_uses_are_highlighted(tmp_path):
    with client(tmp_path, SelectiveModel()) as c:
        d = upload(c, "a.txt", "Mira leads Acme. Acme uses Nova.")
        ready(c, d["id"])
        assert len(c.get("/api/graph").json()["edges"]) == 2
        graph = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()["graph"]
        (edge,) = graph["edges"]  # the relation the answer rests on, not the whole graph
        assert edge["label"] == "leads"
        assert graph["highlighted_edge_ids"] == [edge["id"]]
        assert set(graph["highlighted_node_ids"]) == {edge["source"], edge["target"]}
        assert {n["id"] for n in graph["nodes"]} == {edge["source"], edge["target"]}


def test_graph_edges_point_to_passages_instead_of_embedding_them(tmp_path):
    with client(tmp_path) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        (edge,) = c.get("/api/graph").json()["edges"]
        (evidence,) = edge["evidence"]
        assert set(evidence) == {
            "chunk_id",
            "document_id",
            "document_name",
            "quote",
            "page",
            "heading",
        }
        assert len(json.dumps(edge)) < 600  # an edge costs bytes, not passages
        assert c.get("/api/sources/" + evidence["chunk_id"]).json()["text"] == "Mira leads Acme."


def test_graph_does_not_read_document_text(tmp_path, monkeypatch):
    """The UI polls /graph; building it must not load every document to look for source URLs."""
    with client(tmp_path) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])

        def fail(*args, **kwargs):
            raise AssertionError("the graph read document text")

        monkeypatch.setattr(ChunkRepo, "source_urls_for", fail)
        assert len(c.get("/api/graph").json()["edges"]) == 1
        entity = c.get("/api/graph").json()["nodes"][0]["id"]
        assert len(c.get("/api/entities/" + entity).json()["relations"]) == 1


def test_messages_store_highlight_ids_not_graph_snapshots(tmp_path):
    with client(tmp_path, SelectiveModel()) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme. Acme uses Nova.")["id"])
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        highlights = {
            "highlighted_node_ids": reply["graph"]["highlighted_node_ids"],
            "highlighted_edge_ids": reply["graph"]["highlighted_edge_ids"],
        }
        assert highlights["highlighted_edge_ids"]
        stored = c.app.state.ctx.store.conversations.messages(reply["conversation_id"])
        assert stored[1]["graph"] == highlights
        history = c.get("/api/conversations/" + reply["conversation_id"]).json()
        assert history["messages"][1]["graph"] == highlights


def test_history_saved_with_a_full_graph_snapshot_still_loads(tmp_path):
    snapshot = {
        "nodes": [
            {"id": "n", "label": "Mira", "type": "person", "document_ids": [], "mentions": 1}
        ],
        "edges": [],
        "highlighted_node_ids": ["n"],
        "highlighted_edge_ids": ["e"],
    }
    with client(tmp_path) as c:
        c.app.state.ctx.store.conversations.save_exchange(
            "old",
            new=True,
            title="Who leads Acme?",
            messages=[
                {"role": "user", "content": "Who leads Acme?"},
                {"role": "assistant", "content": "Mira.", "citations": [], "graph": snapshot},
            ],
        )
        graph = c.get("/api/conversations/old").json()["messages"][1]["graph"]
        assert graph == {"highlighted_node_ids": ["n"], "highlighted_edge_ids": ["e"]}


class UnrelatedQuoteModel(FakeModel):
    def extract(self, prompt):
        result = super().extract(prompt)
        result["relations"][0]["quote"] = "Nova owns Vega."
        return result


def test_quote_must_contain_an_endpoint(tmp_path):
    with client(tmp_path, UnrelatedQuoteModel()) as c:
        d = upload(c, "a.txt", "Mira leads Acme. Nova owns Vega.")
        assert ready(c, d["id"])["warnings"]
        assert c.get("/api/graph").json()["edges"] == []


def test_prompt_carries_compact_history_labelled_sources_and_graph_facts(tmp_path):
    model = RecordingModel()
    with client(tmp_path, model) as c:
        d = upload(c, "roster.md", "# Roster\n\nMira leads Acme.")
        ready(c, d["id"])
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        c.post(
            "/api/chat",
            json={"question": "What does she lead?", "conversation_id": reply["conversation_id"]},
        )
        prompt = model.answer_prompts()[-1]
        history = json.loads(prompt.split("HISTORY:\n")[1].split("\n\nQUESTION:")[0])
        assert all(set(m) == {"role", "content"} for m in history)
        assert "CHUNK S1 | roster.md › Roster" in prompt  # sources are attributed by document
        assert (
            '- Mira leads Acme [S1] "Mira leads Acme."' in prompt
        )  # graph facts cite their source
        assert "homepage" not in prompt.casefold() and "niruthi" not in prompt.casefold()
        assert len(prompt.encode()) < 8000


class StallingModel(FakeModel):
    def __init__(self):
        self.first = True

    async def embed(self, texts):
        import asyncio

        if self.first:
            self.first = False
            await asyncio.sleep(1)
        return await super().embed(texts)


def test_total_job_timeout_does_not_kill_worker(tmp_path):
    with client(tmp_path, StallingModel(), model_timeout=2, job_timeout=0.08) as c:
        first = upload(c, "a.txt", "Mira leads Acme.")
        second = upload(c, "b.txt", "Mira leads Acme. Again.")
        assert "timed out" in ready(c, first["id"])["error"]
        assert ready(c, second["id"])["status"] == "ready"


def test_failed_staged_replacement_retries_after_restart(tmp_path):
    settings = Settings(data_dir=tmp_path)
    model = RecordingModel()
    with TestClient(create_app(settings, model)) as c:
        d = upload(c, "original.txt", "Mira leads Acme.")
        ready(c, d["id"])
        model.offline = True
        c.put(
            "/api/documents/" + d["id"],
            files={"file": ("replacement.txt", b"Mira leads Acme. New detail.")},
        )
        failed = ready(c, d["id"])
        assert failed["name"] == "original.txt" and failed["content_hash"] == d["content_hash"]
        assert list(tmp_path.glob(d["content_hash"] + ".txt"))
    model.offline = False
    with TestClient(create_app(settings, model)) as c:
        retried = c.put(
            "/api/documents/" + d["id"],
            files={"file": ("replacement.txt", b"Mira leads Acme. New detail.")},
        )
        assert retried.status_code == 202
        assert ready(c, d["id"])["name"] == "replacement.txt"
        assert not list(tmp_path.glob(d["content_hash"] + ".txt"))


def test_full_question_and_complete_recent_history_reach_the_prompt(tmp_path):
    model = RecordingModel()
    with client(tmp_path, model) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        question = "Context " * 150 + " Who leads Acme?"
        reply = c.post("/api/chat", json={"question": question}).json()
        assert question in model.answer_prompts()[-1]
        c.post(
            "/api/chat",
            json={"question": "What does she lead?", "conversation_id": reply["conversation_id"]},
        )
        history = json.loads(
            model.answer_prompts()[-1].split("HISTORY:\n")[1].split("\n\nQUESTION:")[0]
        )
        assert history[0]["content"] == question


def test_non_ascii_over_budget_question_is_explicit(tmp_path):
    with client(tmp_path, RecordingModel()) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        response = c.post("/api/chat", json={"question": "🙂" * 2000})
        assert response.status_code in {200, 413, 422}
        if response.status_code == 200:
            assert response.json()["citations"]


class CrowdedModel(RecordingModel):
    def extract(self, prompt):
        relations = [
            {
                "subject": f"Alpha{i}",
                "predicate": "uses",
                "object": f"Beta{i}",
                "phrase": "uses",
                "quote": f"Alpha{i} uses Beta{i}.",
            }
            for i in range(30)
        ]
        relations.append(
            {
                "subject": "Mira",
                "predicate": "leads",
                "object": "Acme",
                "phrase": "leads",
                "quote": "Mira leads Acme.",
            }
        )
        entities = [{"name": f"Alpha{i}", "type": "Organization"} for i in range(30)]
        entities += [{"name": f"Beta{i}", "type": "Product"} for i in range(30)]
        return {
            "entities": entities
            + [{"name": "Mira", "type": "Person"}, {"name": "Acme", "type": "Organization"}],
            "relations": relations,
        }


def test_facts_about_the_questions_entities_outrank_unrelated_facts(tmp_path):
    model = CrowdedModel()
    text = " ".join(f"Alpha{i} uses Beta{i}." for i in range(30)) + " Mira leads Acme."
    with client(tmp_path, model) as c:
        ready(c, upload(c, "a.txt", text)["id"])
        c.post("/api/chat", json={"question": "Who leads Acme?"})
        facts = model.answer_prompts()[-1].split("FACTS:\n")[1].split("\n\n")[0].splitlines()
        assert len(facts) <= 24 and facts[0].startswith("- Mira leads Acme")


def test_retrieval_prefers_content_overlap_over_stopword_decoys(tmp_path):
    content = b"Mira leads Acme.\nNiruthi lists Founder Dr. Rama Nemani on the homepage.\n"
    decoy = b"It is at the top of the list, as any policy of the file notes.\n" * 2
    with client(tmp_path, RecordingModel()) as c:
        ready(c, upload(c, "content.md", content)["id"])
        ready(c, upload(c, "decoy.md", decoy)["id"])
        reply = c.post(
            "/api/chat", json={"question": "Who is listed as the Founder of Niruthi?"}
        ).json()
        assert reply["citations"][0]["document_name"] == "content.md"


def test_citation_public_urls_come_from_document_headers(tmp_path):
    text = "Source: https://example.org/about\nMira leads Acme.\nSource: https://example.org/team\n"
    with client(tmp_path) as c:
        ready(c, upload(c, "public.txt", text)["id"])
        evidence = c.get("/api/graph").json()["edges"][0]["evidence"][0]
        source = c.get("/api/sources/" + evidence["chunk_id"]).json()
        assert source["source_urls"] == ["https://example.org/about", "https://example.org/team"]


class MissingCitationModel(FakeModel):
    def answer(self, prompt):
        return {"answer": "Mira leads Acme.", "citation_ids": []}


def test_retrieved_evidence_without_model_citations_is_visible(tmp_path):
    with client(tmp_path, MissingCitationModel()) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        assert reply["citations"] == [] and reply["answer"] != "Mira leads Acme."
        assert reply["status"] == "insufficient_evidence"
        assert any("omitted citation IDs" in warning for warning in reply["warnings"])


class RetryingModel(FakeModel):
    def __init__(self):
        self.answers = 0

    def answer(self, prompt):
        self.answers += 1
        if "REMINDER" not in prompt:
            return {"answer": "Mira leads Acme.", "citation_ids": []}
        return super().answer(prompt)


def test_uncited_answer_gets_one_corrective_retry_before_being_suppressed(tmp_path):
    model = RetryingModel()
    with client(tmp_path, model) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        assert reply["answer"] == "Mira leads Acme." and reply["citations"] and model.answers == 2


class AliasCitationModel(FakeModel):
    def answer(self, prompt):
        assert "CHUNK S1" in prompt
        return {"answer": "Mira leads Acme.", "citation_ids": ["S1", "S9"]}

    async def generate(self, prompt, schema, max_tokens=None):
        if "citation_ids" in schema.get("properties", {}):
            assert schema["properties"]["citation_ids"]["items"]["enum"][0] == "S1"
            assert {"answer", "citation_ids"} <= set(schema["required"])
        return await super().generate(prompt, schema)


def test_short_source_alias_maps_to_real_source_and_unknown_aliases_are_discarded(tmp_path):
    with client(tmp_path, AliasCitationModel()) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        assert reply["answer"] == "Mira leads Acme."
        (citation,) = reply["citations"]
        assert citation["chunk_id"] != "S1"
        assert c.get("/api/sources/" + citation["chunk_id"]).json()["text"] == "Mira leads Acme."
        assert any("unknown citation IDs" in w for w in reply["warnings"])


class OmittedCitationKeyModel(FakeModel):
    def answer(self, prompt):
        return {"answer": "Mira leads Acme."}


def test_omitted_citation_field_is_explicit_model_error(tmp_path):
    with client(tmp_path, OmittedCitationKeyModel()) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        response = c.post("/api/chat", json={"question": "Who leads Acme?"})
        assert response.status_code == 503
        assert "omitted required citation_ids" in response.json()["detail"]


def test_demo_loads_public_and_synthetic_testing_docs(tmp_path):
    repo = tmp_path / "repo"
    for folder, prefix, count in [("documents", "public", 6), ("testing", "fictional", 5)]:
        path = repo / "fixtures" / folder
        path.mkdir(parents=True)
        for i in range(count):
            (path / f"{prefix}-{i}.md").write_text(f"Mira leads Acme. {prefix} test {i}.")
    with client(tmp_path / "data", fixtures_dir=repo / "fixtures") as c:
        response = c.post("/api/demo")
        assert response.status_code == 202 and len(response.json()["documents"]) == 11
        docs = c.post("/api/demo").json()["documents"]
        assert len(docs) == 11
        assert sorted(d["labels"] for d in docs) == [[]] * 6 + [["synthetic"]] * 5


def test_answers_citing_labelled_documents_carry_the_configured_notice(tmp_path):
    notice = "Test data only."
    with client(tmp_path, label_notices={"synthetic": notice}) as c:
        d = upload(
            c, "scenario.txt", "FICTIONAL SCENARIO\nMira leads Acme.", labels="Synthetic, ,demo"
        )
        assert d["labels"] == ["demo", "synthetic"]
        ready(c, d["id"])
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        assert reply["answer"].startswith(notice) and "Mira leads Acme." in reply["answer"]


def test_unlabelled_answer_has_no_notice(tmp_path):
    with client(tmp_path) as c:
        ready(c, upload(c, "public.txt", "Source: https://example.org/\nMira leads Acme.")["id"])
        assert (
            c.post("/api/chat", json={"question": "Who leads Acme?"}).json()["answer"]
            == "Mira leads Acme."
        )


def test_excluding_a_label_removes_those_documents_from_retrieval(tmp_path):
    with client(tmp_path) as c:
        ready(
            c,
            upload(
                c, "fiction.md", "Mira leads Acme. Leave policy is invented.", labels="synthetic"
            )["id"],
        )
        reply = c.post(
            "/api/chat",
            json={"question": "What is the leave policy?", "exclude_labels": ["synthetic"]},
        ).json()
        assert reply["citations"] == [] and "Mira leads Acme." not in reply["answer"]
        assert reply["graph"]["highlighted_edge_ids"] == []
        included = c.post("/api/chat", json={"question": "What is the leave policy?"}).json()
        assert included["citations"]


def test_full_document_reader_preserves_text_and_failed_replacement(tmp_path):
    text = "FICTIONAL TEST SCENARIO\n" + "".join(
        f"Mira leads Acme. Unique paragraph {i}.\n" for i in range(30)
    )
    with client(tmp_path) as c:
        d = upload(c, "original.md", text)
        ready(c, d["id"])
        content = c.get("/api/documents/" + d["id"] + "/content").json()
        assert content["text"] == text and content["text_kind"] == "extracted"
        assert len(content["chunks"]) > 1 and not content["showing_last_published_version"]
        c.put("/api/documents/" + d["id"], files={"file": ("failed.md", b"\xff")})
        ready(c, d["id"])
        content = c.get("/api/documents/" + d["id"] + "/content").json()
        assert content["text"] == text and content["document"]["name"] == "original.md"
        assert content["showing_last_published_version"]
        assert "last successfully published" in content["provenance_note"]
        c.delete("/api/documents/" + d["id"])
        assert c.get("/api/documents/" + d["id"] + "/content").status_code == 404


def test_extraction_artifacts_are_normalised_before_indexing(tmp_path):
    raw = "Acme &amp; Co<!-- image -->\r\n\r\n\r\n\r\nMira leads Acme.\n"
    with client(tmp_path) as c:
        d = upload(c, "docling.md", raw)
        ready(c, d["id"])
        content = c.get("/api/documents/" + d["id"] + "/content").json()
        assert content["text"] == "Acme & Co\n\nMira leads Acme.\n"


def test_document_progress_and_chat_status_stages(tmp_path):
    with client(tmp_path) as c:
        d = upload(c, "a.txt", "Mira leads Acme.")
        assert d["processing_stage"] == "queued" and d["pipeline_version"] == 1
        completed = ready(c, d["id"])
        assert (
            completed["processing_stage"] == "ready"
            and completed["pipeline_version"] == PIPELINE_VERSION
        )
        assert (
            completed["processed_chunks"] == completed["total_chunks"] == completed["chunk_count"]
        )
        stream = c.post("/api/chat/stream", json={"question": "Who leads Acme?"}).text
        for stage in ["retrieving", "embedding_query", "generating", "validating", "streaming"]:
            assert '"stage": "' + stage + '"' in stream
        assert stream.index('"stage": "validating"') < stream.index("event: token")
        result = json.loads(re.search(r"event: result\ndata: (.*)\n", stream).group(1))
        assert result["status"] == "answered" and result["plan"]["intent"] == "relationship"


def test_follow_up_questions_are_rewritten_for_retrieval(tmp_path):
    model = RecordingModel()
    with client(tmp_path, model) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        follow = c.post(
            "/api/chat",
            json={"question": "And what does she do?", "conversation_id": reply["conversation_id"]},
        ).json()
        assert any("FINAL QUESTION" in p for p in model.prompts)
        assert follow["plan"]["search_text"]


def test_sse_disconnect_cancels_generation(tmp_path):
    import asyncio

    import httpx
    from knowledge_engine.api.chat import chat_stream
    from knowledge_engine.schemas.chat import Question

    async def scenario():
        class CancelModel(FakeModel):
            def __init__(self):
                self.started = asyncio.Event()
                self.cancelled = asyncio.Event()

            async def generate(self, prompt, schema, max_tokens=None):
                if "citation_ids" not in schema.get("properties", {}):
                    return await super().generate(prompt, schema)
                self.started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
                    raise

        model = CancelModel()
        app = create_app(Settings(data_dir=tmp_path), model)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as c,
        ):
            d = (
                await c.post("/api/documents", files={"file": ("a.txt", b"Mira leads Acme.")})
            ).json()
            for _ in range(300):
                docs = (await c.get("/api/documents")).json()["documents"]
                if next(x for x in docs if x["id"] == d["id"])["status"] == "ready":
                    break
                await asyncio.sleep(0.01)
            response = await chat_stream(Question(question="Who leads Acme?"), app.state.ctx)
            iterator = response.body_iterator
            await anext(iterator)
            await asyncio.wait_for(model.started.wait(), 1)
            await iterator.aclose()
            assert model.cancelled.is_set()
            assert (await c.get("/api/conversations")).json()["conversations"] == []

    asyncio.run(scenario())


def test_health_reports_models_index_and_openapi_documents_responses(tmp_path):
    with client(tmp_path) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        body = c.get("/api/health").json()
        assert body["ollama_available"] is True and body["warnings"] == []
        assert body["index"] == {
            "documents": 1,
            "stale_documents": 0,
            "chunks": 1,
            "entities": 2,
            "relations": 1,
        }
        assert c.get("/api/documents/missing/content").json() == {"detail": "Document not found"}
        schemas = c.get("/openapi.json").json()["components"]["schemas"]
        assert {"ChatResponse", "Document", "KnowledgeGraph", "Health", "EntityDetail"} <= set(
            schemas
        )


def test_entities_are_browsable_with_aliases_documents_and_evidence(tmp_path):
    with client(tmp_path) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        listed = c.get("/api/entities").json()["entities"]
        assert {e["name"] for e in listed} == {"Mira", "Acme"} and listed[0]["degree"] == 1
        assert [e["name"] for e in c.get("/api/entities?query=mir").json()["entities"]] == ["Mira"]
        assert c.get("/api/entities?type=Organization").json()["entities"][0]["name"] == "Acme"
        mira = next(e for e in listed if e["name"] == "Mira")
        detail = c.get("/api/entities/" + mira["id"]).json()
        assert detail["documents"][0]["name"] == "a.txt"
        assert detail["relations"][0]["evidence"][0]["quote"] == "Mira leads Acme."
        assert c.get("/api/entities/nope").status_code == 404


def test_search_endpoint_exposes_retrieval_without_generating_an_answer(tmp_path):
    model = RecordingModel()
    with client(tmp_path, model) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        ready(c, upload(c, "b.txt", "Unrelated holiday calendar text.")["id"])
        before = len(model.prompts)
        result = c.post("/api/search", json={"query": "Who leads Acme?", "k": 2}).json()
        assert result["hits"][0]["document_name"] == "a.txt"
        assert {"dense", "sparse"} <= set(result["hits"][0]["ranks"])
        assert result["plan"]["entities"][0]["name"] == "Acme" and "terms" in result["plan"]
        assert len(model.prompts) == before  # no answer or rewrite call was made
        assert c.post("/api/search", json={"query": ""}).status_code == 422


def test_reindex_requeues_documents_built_by_an_older_pipeline(tmp_path):
    with client(tmp_path) as c:
        d = upload(c, "a.txt", "Mira leads Acme.")
        ready(c, d["id"])
        assert c.post("/api/documents/reindex").json() == {"queued": 0}
        c.app.state.ctx.store.db.run("UPDATE documents SET pipeline_version = 1")
        health = c.get("/api/health").json()
        assert health["index"]["stale_documents"] == 1 and "reindex" in health["warnings"][0]
        assert c.post("/api/documents/reindex").json() == {"queued": 1}
        assert ready(c, d["id"])["pipeline_version"] == PIPELINE_VERSION


class ConflictModel(FakeModel):
    def extract(self, prompt):
        return {
            "entities": [
                {"name": "Mira", "type": "Person"},
                {"name": "Anil", "type": "Person"},
                {"name": "Bob", "type": "Person"},
            ],
            "relations": [
                {
                    "subject": "Mira",
                    "predicate": "reports_to",
                    "object": "Anil",
                    "phrase": "reports to",
                    "quote": "Mira reports to Anil.",
                },
                {
                    "subject": "Mira",
                    "predicate": "reports_to",
                    "object": "Bob",
                    "phrase": "reports to",
                    "quote": "Mira reports to Bob.",
                },
            ],
        }


def test_conflicting_evidence_is_surfaced_not_resolved_silently(tmp_path):
    model = ConflictModel()
    with client(tmp_path, model) as c:
        ready(c, upload(c, "a.txt", "Mira reports to Anil.")["id"])
        ready(c, upload(c, "b.txt", "Mira reports to Bob.")["id"])
        reply = c.post("/api/chat", json={"question": "Who does Mira report to?"}).json()
        assert reply["status"] == "conflicting_evidence"
        assert reply["plan"]["conflicts"] == [
            {"subject": "Mira", "predicate": "reports_to", "objects": ["Anil", "Bob"]}
        ]


def test_startup_does_not_block_when_more_documents_are_queued_than_the_queue_holds(tmp_path):
    settings = Settings(data_dir=tmp_path, max_jobs=2)
    with TestClient(create_app(settings, FakeModel())) as c:
        ids = [upload(c, f"d{i}.txt", f"Mira leads Acme {i}.")["id"] for i in range(2)]
        for id in ids:
            ready(c, id)
        store = c.app.state.ctx.store
        for i in range(5):  # five documents left queued by a previous run
            store.db.run(
                "INSERT INTO documents(id,name,content_hash,status,path) VALUES(%s,%s,%s,'queued',%s)",
                (f"q{i}", f"q{i}.txt", f"h{i}", str(tmp_path / f"q{i}.txt")),
            )
            (tmp_path / f"q{i}.txt").write_text(f"Mira leads Acme queued {i}.")
    with TestClient(create_app(settings, FakeModel())) as c:  # restart: 5 queued > queue size 2
        for i in range(5):
            assert ready(c, f"q{i}")["status"] == "ready"
