import json
import re

from fakes import FakeModel, ready
from fastapi.testclient import TestClient
from knowledge_engine.app import create_app
from knowledge_engine.config import Settings
from knowledge_engine.pipeline.indexing import PIPELINE_VERSION


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
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        assert reply["citations"] and reply["status"] == "answered"
        follow = c.post(
            "/api/chat",
            json={"question": "Where does she work?", "conversation_id": reply["conversation_id"]},
        )
        assert follow.status_code == 200
        c.delete("/api/documents/" + doc["id"])
        assert c.get("/api/documents").json()["documents"] == []
        assert c.post("/api/search", json={"query": "Mira"}).json()["hits"] == []


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
        (hit,) = c.post("/api/search", json={"query": "Mira leads"}).json()["hits"]
        assert hit["document_name"] == d["name"]  # the published version stays searchable
        c.put("/api/documents/" + d["id"], files={"file": ("new.txt", b"Acme employs Mira.")})
        assert ready(c, d["id"])["status"] == "ready"
        (hit,) = c.post("/api/search", json={"query": "Mira"}).json()["hits"]
        assert hit["text"] == "Acme employs Mira."


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
        (hit,) = c.post("/api/search", json={"query": "Mira"}).json()["hits"]
        source = c.get("/api/sources/" + hit["chunk_id"]).json()
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

    async def generate(self, prompt, schema, max_tokens=None, think=False, model=None):
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


def test_messages_store_the_trace_and_statements(tmp_path):
    with client(tmp_path) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        (passage,) = reply["trace"]["passages"]
        assert passage["document"] == "a.txt" and {"dense", "sparse"} <= set(passage["ranks"])
        assert passage["snippet"] == "Mira leads Acme."
        assert reply["trace"]["counts"]["candidates"] == 1
        assert reply["statements"] == [
            {"text": "Mira leads Acme.", "sources": [reply["citations"][0]["chunk_id"]]}
        ]
        history = c.get("/api/conversations/" + reply["conversation_id"]).json()
        assert history["messages"][1]["trace"] == reply["trace"]
        assert history["messages"][1]["statements"] == reply["statements"]


def test_history_saved_with_a_graph_field_still_loads(tmp_path):
    with client(tmp_path) as c:
        c.app.state.ctx.store.conversations.save_exchange(
            "old",
            new=True,
            title="Who leads Acme?",
            messages=[
                {"role": "user", "content": "Who leads Acme?"},
                {
                    "role": "assistant",
                    "content": "Mira.",
                    "citations": [],
                    "graph": {"highlighted_node_ids": ["n"], "highlighted_edge_ids": ["e"]},
                },
            ],
        )
        message = c.get("/api/conversations/old").json()["messages"][1]
        assert message["content"] == "Mira." and "graph" not in message


class CorroboratingModel(FakeModel):
    """Cites only the first passage; the statement check finds the second one too."""

    def answer(self, prompt):
        return {"answer": "Mira leads Acme.", "citation_ids": ["S1"], "status": "answered"}

    async def generate(self, prompt, schema, max_tokens=None, think=False, model=None):
        if "statements" in schema.get("properties", {}):
            return {"statements": [{"n": 1, "sources": ["S1", "S2"]}]}
        return await super().generate(prompt, schema)


def test_the_statement_check_completes_the_citations(tmp_path):
    with client(tmp_path, CorroboratingModel()) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        ready(c, upload(c, "b.txt", "Acme is led by Mira.")["id"])
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        assert {x["document_name"] for x in reply["citations"]} == {"a.txt", "b.txt"}
        (statement,) = reply["statements"]
        assert len(statement["sources"]) == 2
    with client(tmp_path / "off", CorroboratingModel(), verify_citations=False) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        ready(c, upload(c, "b.txt", "Acme is led by Mira.")["id"])
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        assert len(reply["citations"]) == 1 and reply["statements"] == []


def test_prompt_carries_compact_history_and_labelled_sources(tmp_path):
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
        (hit,) = c.post("/api/search", json={"query": "Mira"}).json()["hits"]
        source = c.get("/api/sources/" + hit["chunk_id"]).json()
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

    async def generate(self, prompt, schema, max_tokens=None, think=False, model=None):
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
        stages = ["retrieving", "embedding_query", "generating", "validating", "verifying"]
        for stage in [*stages, "streaming"]:
            assert '"stage": "' + stage + '"' in stream
        assert stream.index('"stage": "validating"') < stream.index("event: token")
        # the model's answer streams as drafts while it is generated, before it is validated
        drafts = [json.loads(d)["text"] for d in re.findall(r"event: draft\ndata: (.*)\n", stream)]
        assert drafts and drafts[-1] == "Mira leads Acme."
        assert stream.index("event: draft") < stream.index('"stage": "validating"')
        result = json.loads(re.search(r"event: result\ndata: (.*)\n", stream).group(1))
        assert result["status"] == "answered" and result["trace"]["intent"] == "relationship"


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
        assert follow["trace"]["search_text"]


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

            async def generate(
                self, prompt, schema, max_tokens=None, think=False, model=None, on_text=None
            ):
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
        assert body["index"] == {"documents": 1, "stale_documents": 0, "chunks": 1}
        assert c.get("/api/documents/missing/content").json() == {"detail": "Document not found"}
        schemas = c.get("/openapi.json").json()["components"]["schemas"]
        assert {"ChatResponse", "AnswerTrace", "Document", "Health", "Preview"} <= set(schemas)
        assert c.get("/api/entities").status_code == 404


def test_search_endpoint_exposes_retrieval_without_generating_an_answer(tmp_path):
    model = RecordingModel()
    with client(tmp_path, model) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        ready(c, upload(c, "b.txt", "Unrelated holiday calendar text.")["id"])
        before = len(model.prompts)
        result = c.post("/api/search", json={"query": "Who leads Acme?", "k": 2}).json()
        assert result["hits"][0]["document_name"] == "a.txt"
        assert {"dense", "sparse"} <= set(result["hits"][0]["ranks"])
        assert "terms" in result["plan"]
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
    def answer(self, prompt):
        return {**super().answer(prompt), "status": "conflicting_evidence"}


def test_conflicting_evidence_reported_by_the_model_is_surfaced(tmp_path):
    with client(tmp_path, ConflictModel()) as c:
        ready(c, upload(c, "a.txt", "Mira reports to Anil.")["id"])
        reply = c.post("/api/chat", json={"question": "Who does Mira report to?"}).json()
        assert reply["status"] == "conflicting_evidence" and reply["citations"]


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


class EnglishFirstModel(RecordingModel):
    """Answers a Hindi question in English first, in Hindi when reminded."""

    def answer(self, prompt):
        hindi = "REMINDER: your previous answer was not in the language" in prompt
        text = "मीरा एक्मे का नेतृत्व करती हैं।" if hindi else "Mira leads Acme."
        return {"answer": text, "citation_ids": ["S1"], "status": "answered"}


def test_a_question_in_an_indian_script_is_answered_in_it(tmp_path):
    model = EnglishFirstModel()
    with client(tmp_path, model) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        reply = c.post("/api/chat", json={"question": "एक्मे का नेतृत्व कौन करता है?"}).json()
        assert reply["answer"] == "मीरा एक्मे का नेतृत्व करती हैं।"
        assert "LANGUAGE: the QUESTION is written in Devanagari script" in model.answer_prompts()[0]
        english = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        assert english["answer"] == "Mira leads Acme."  # Latin-script questions: no retry
        assert "LANGUAGE:" not in model.answer_prompts()[-1]


class TeluguWhenToldModel(RecordingModel):
    """Answers in the question's language until reminded of the chosen one."""

    def answer(self, prompt):
        telugu = "REMINDER: your previous answer was not in Telugu" in prompt
        text = "మీరా ఆక్మేకు నాయకత్వం వహిస్తారు." if telugu else "Mira leads Acme."
        return {"answer": text, "citation_ids": ["S1"], "status": "answered"}


def test_a_chosen_answer_language_overrides_the_question_language(tmp_path):
    model = TeluguWhenToldModel()
    with client(tmp_path, model) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        assert "Telugu" in c.get("/api/languages").json()["languages"]
        body = {"question": "Who leads Acme?", "answer_language": "Telugu"}
        reply = c.post("/api/chat", json=body).json()
        assert reply["answer"] == "మీరా ఆక్మేకు నాయకత్వం వహిస్తారు."  # English reply retried
        assert "write the whole answer in Telugu, in Telugu script" in model.answer_prompts()[0]
        # a chosen language is enforced even when it is English and the question is not
        model.prompts.clear()
        english = c.post(
            "/api/chat",
            json={"question": "एक्मे का नेतृत्व कौन करता है?", "answer_language": "English"},
        ).json()
        assert english["answer"] == "Mira leads Acme."
        assert "write the whole answer in English" in model.answer_prompts()[0]
        assert len(model.answer_prompts()) == 1  # already English: no retry
        bad = c.post("/api/chat", json={"question": "Who?", "answer_language": "Klingon"})
        assert bad.status_code == 422


def test_the_stream_sends_the_growing_trace_and_the_final_timings(tmp_path):
    with client(tmp_path) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        stream = c.post("/api/chat/stream", json={"question": "Who leads Acme?"}).text
        traces = [json.loads(m) for m in re.findall(r"event: trace\ndata: (.*)\n", stream)]
        assert [len(t["passages"]) for t in traces] == [0, 1]  # understood, then retrieved
        assert traces[0]["search_text"] == "Who leads Acme?" and "timings" not in traces[0]
        assert stream.index("event: trace") < stream.index("event: token")
        result = json.loads(re.search(r"event: result\ndata: (.*)\n", stream).group(1))
        timings = result["trace"]["timings"]
        stages = ("understand_ms", "retrieve_ms", "answer_ms", "check_ms")
        assert set(timings) == {*stages, "total_ms"} and all(v >= 0 for v in timings.values())
        assert sum(timings[k] for k in stages) <= timings["total_ms"] + 4  # rounding
