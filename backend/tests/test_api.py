import time

from company_assistant.main import Settings, create_app
from fastapi.testclient import TestClient


class FakeModel:
    async def health(self):
        return ["qwen3.5:9b", "all-minilm:l6-v2"]

    async def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]

    async def generate(self, prompt, schema):
        if "relations" in schema.get("properties", {}):
            return {
                "entities": [
                    {"name": "Acme", "type": "company"},
                    {"name": "Mira", "type": "person"},
                ],
                "relations": [
                    {
                        "source": "Mira",
                        "target": "Acme",
                        "label": "leads",
                        "quote": "Mira leads Acme.",
                    }
                ],
            }
        import re

        ids = re.findall(r"CHUNK ([0-9a-f-]{36})", prompt)
        return {"answer": "Mira leads Acme.", "citation_ids": ids[:1]}


def ready(client, id):
    for _ in range(100):
        doc = next(x for x in client.get("/api/documents").json()["documents"] if x["id"] == id)
        if doc["status"] in ["ready", "failed"]:
            return doc
        time.sleep(0.01)
    raise AssertionError("processing timed out")


def test_document_lifecycle(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), FakeModel())) as c:
        doc = c.post("/api/documents", files={"file": ("a.txt", b"Mira leads Acme.")}).json()
        assert ready(c, doc["id"])["status"] == "ready"
        assert (
            c.post("/api/documents", files={"file": ("b.txt", b"Mira leads Acme.")}).json()["id"]
            == doc["id"]
        )
        assert len(c.get("/api/graph").json()["edges"]) == 1
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        assert reply["citations"]
        assert (
            c.post(
                "/api/chat",
                json={
                    "question": "Where does she work?",
                    "conversation_id": reply["conversation_id"],
                },
            ).status_code
            == 200
        )
        c.delete("/api/documents/" + doc["id"])
        assert c.get("/api/graph").json()["edges"] == []


def test_invalid_quotes(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), FakeModel())) as c:
        d = c.post("/api/documents", files={"file": ("a.txt", b"Acme employs Mira.")}).json()
        assert ready(c, d["id"])["warnings"]
        assert c.get("/api/graph").json()["edges"] == []


class OfflineModel(FakeModel):
    async def embed(self, texts):
        raise RuntimeError("Ollama is offline")


def test_failed_replacement_keeps_evidence(tmp_path):
    model = FakeModel()
    with TestClient(create_app(Settings(data_dir=tmp_path), model)) as c:
        d = c.post("/api/documents", files={"file": ("a.txt", b"Mira leads Acme.")}).json()
        ready(c, d["id"])
        c.put("/api/documents/" + d["id"], files={"file": ("bad.txt", b"\xff")})
        failed = ready(c, d["id"])
        assert failed["status"] == "failed"
        assert failed["name"] == d["name"]
        assert failed["content_hash"] == d["content_hash"]
        assert c.get("/api/graph").json()["edges"][0]["evidence"][0]["document_name"] == d["name"]
        assert len(c.get("/api/graph").json()["edges"]) == 1
        c.put("/api/documents/" + d["id"], files={"file": ("new.txt", b"Acme employs Mira.")})
        assert ready(c, d["id"])["status"] == "ready"
        assert c.get("/api/graph").json()["edges"] == []


def test_offline_job_and_stream_error(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), OfflineModel())) as c:
        d = c.post("/api/documents", files={"file": ("a.txt", b"Mira leads Acme.")}).json()
        assert "offline" in ready(c, d["id"])["error"]
        # No evidence should yield an honest abstention without inventing a model result.
        response = c.post("/api/chat/stream", json={"question": "Who leads Acme?"})
        assert "event: status" in response.text
        assert "event: result" in response.text
        assert "could not find supporting evidence" in response.text


def test_upload_limits_and_sources(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, max_upload_bytes=20), FakeModel())) as c:
        assert c.post("/api/documents", files={"file": ("a.txt", b"x" * 21)}).status_code == 413
        assert c.post("/api/documents", files={"file": ("a.exe", b"x")}).status_code == 400
        d = c.post("/api/documents", files={"file": ("a.txt", b"Mira leads Acme.")}).json()
        ready(c, d["id"])
        citation = c.get("/api/graph").json()["edges"][0]["evidence"][0]
        assert c.get("/api/sources/" + citation["chunk_id"]).json()["text"] == "Mira leads Acme."
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

    async def generate(self, prompt, schema):
        self.prompts.append(prompt)
        return await super().generate(prompt, schema)


def test_persisted_followups_dedup_and_chat_failure(tmp_path):
    model = RecordingModel()
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings, model)) as c:
        d = c.post("/api/documents", files={"file": ("a.txt", b"Mira leads Acme.")}).json()
        ready(c, d["id"])
        calls = model.calls
        c.post("/api/documents", files={"file": ("copy.txt", b"Mira leads Acme.")})
        assert model.calls == calls
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        cid = reply["conversation_id"]
    with TestClient(create_app(settings, model)) as c:
        reply = c.post(
            "/api/chat", json={"question": "What does she lead?", "conversation_id": cid}
        )
        assert reply.status_code == 200
        assert "Who leads Acme?" in model.prompts[-1]
        assert len(c.get("/api/conversations/" + cid).json()["messages"]) == 4
        model.offline = True
        assert c.post("/api/chat", json={"question": "Acme?"}).status_code == 503
        stream = c.post("/api/chat/stream", json={"question": "Acme?"}).text
        assert "event: error" in stream
        assert "offline" in stream


class SelectiveModel(FakeModel):
    async def generate(self, prompt, schema):
        import json

        if "relations" in schema.get("properties", {}):
            return {
                "entities": [
                    {"name": "Mira", "type": "person"},
                    {"name": "Acme", "type": "company"},
                    {"name": "Nova", "type": "company"},
                ],
                "relations": [
                    {
                        "source": "Mira",
                        "target": "Acme",
                        "label": "leads",
                        "quote": "Mira leads Acme.",
                    },
                    {
                        "source": "Acme",
                        "target": "Nova",
                        "label": "owns",
                        "quote": "Acme owns Nova.",
                    },
                ],
            }
        result = await super().generate(prompt, schema)
        edges = json.loads(prompt.split("CANDIDATE RELATIONSHIPS:\n")[1])
        result["relation_ids"] = [next(e["id"] for e in edges if e["label"] == "leads")]
        return result


def test_highlight_only_selected_relation(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), SelectiveModel())) as c:
        d = c.post(
            "/api/documents", files={"file": ("a.txt", b"Mira leads Acme. Acme owns Nova.")}
        ).json()
        ready(c, d["id"])
        result = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        graph = result["graph"]
        assert len(graph["edges"]) == 2
        selected = graph["highlighted_edge_ids"]
        assert len(selected) == 1
        assert next(e for e in graph["edges"] if e["id"] == selected[0])["label"] == "leads"


class UnrelatedQuoteModel(FakeModel):
    async def generate(self, prompt, schema):
        if "relations" in schema.get("properties", {}):
            result = await super().generate(prompt, schema)
            result["relations"][0]["quote"] = "Nova owns Vega."
            result["relations"][0]["label"] = "owns"
            return result
        return await super().generate(prompt, schema)


def test_quote_must_contain_endpoints(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), UnrelatedQuoteModel())) as c:
        d = c.post(
            "/api/documents", files={"file": ("a.txt", b"Mira leads Acme. Nova owns Vega.")}
        ).json()
        assert ready(c, d["id"])["warnings"]
        assert c.get("/api/graph").json()["edges"] == []


def test_prompt_history_and_relations_are_compact_json(tmp_path):
    import json

    model = RecordingModel()
    with TestClient(create_app(Settings(data_dir=tmp_path), model)) as c:
        d = c.post("/api/documents", files={"file": ("a.txt", b"Mira leads Acme.")}).json()
        ready(c, d["id"])
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        c.post(
            "/api/chat",
            json={"question": "What does she lead?", "conversation_id": reply["conversation_id"]},
        )
        prompt = model.prompts[-1]
        history = json.loads(prompt.split("HISTORY:\n")[1].split("\nQUESTION:")[0])
        assert all(set(m) == {"role", "content"} for m in history)
        edges = json.loads(prompt.split("CANDIDATE RELATIONSHIPS:\n")[1])
        assert set(edges[0]) == {
            "id",
            "source_label",
            "target_label",
            "label",
            "quote",
            "chunk_ids",
        }
        assert len(prompt.encode()) < 8500


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
    settings = Settings(data_dir=tmp_path, model_timeout=2, job_timeout=0.08)
    with TestClient(create_app(settings, StallingModel())) as c:
        first = c.post("/api/documents", files={"file": ("a.txt", b"Mira leads Acme.")}).json()
        second = c.post(
            "/api/documents", files={"file": ("b.txt", b"Mira leads Acme. Again.")}
        ).json()
        assert "timed out" in ready(c, first["id"])["error"]
        assert ready(c, second["id"])["status"] == "ready"


class WrongLabelModel(FakeModel):
    async def generate(self, prompt, schema):
        result = await super().generate(prompt, schema)
        if "relations" in schema.get("properties", {}):
            result["relations"][0]["label"] = "owns"
        return result


def test_relation_label_must_be_verbatim(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), WrongLabelModel())) as c:
        d = c.post("/api/documents", files={"file": ("a.txt", b"Mira leads Acme.")}).json()
        assert ready(c, d["id"])["warnings"]
        assert c.get("/api/graph").json()["edges"] == []


def test_failed_staged_replacement_retries_after_restart(tmp_path):
    settings = Settings(data_dir=tmp_path)
    model = RecordingModel()
    with TestClient(create_app(settings, model)) as c:
        d = c.post("/api/documents", files={"file": ("original.txt", b"Mira leads Acme.")}).json()
        ready(c, d["id"])
        model.offline = True
        c.put(
            "/api/documents/" + d["id"],
            files={"file": ("replacement.txt", b"Mira leads Acme. New detail.")},
        )
        failed = ready(c, d["id"])
        assert failed["name"] == "original.txt"
        assert failed["content_hash"] == d["content_hash"]
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


def test_full_question_and_complete_recent_history(tmp_path):
    import json

    model = RecordingModel()
    with TestClient(create_app(Settings(data_dir=tmp_path), model)) as c:
        d = c.post("/api/documents", files={"file": ("a.txt", b"Mira leads Acme.")}).json()
        ready(c, d["id"])
        question = "Context " * 150 + " Who leads Acme?"
        reply = c.post("/api/chat", json={"question": question}).json()
        assert question in model.prompts[-1]
        c.post(
            "/api/chat",
            json={"question": "What does she lead?", "conversation_id": reply["conversation_id"]},
        )
        history = json.loads(model.prompts[-1].split("HISTORY:\n")[1].split("\nQUESTION:")[0])
        assert history[0]["content"] == question


def test_non_ascii_over_budget_question_is_explicit(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), RecordingModel())) as c:
        d = c.post("/api/documents", files={"file": ("a.txt", b"Mira leads Acme.")}).json()
        ready(c, d["id"])
        response = c.post("/api/chat", json={"question": "🙂" * 2000})
        assert response.status_code in {200, 413, 422}
        if response.status_code == 200:
            assert response.json()["citations"]


class ConceptEndpointModel(FakeModel):
    async def generate(self, prompt, schema):
        if "relations" in schema.get("properties", {}):
            return {
                "entities": [{"name": "Mira", "type": "person"}],
                "relations": [
                    {
                        "source": "Mira",
                        "target": "postmortem",
                        "label": "reviews",
                        "quote": "Mira reviews the postmortem.",
                    }
                ],
            }
        return await super().generate(prompt, schema)


def test_recover_literal_grounded_concept_endpoint(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), ConceptEndpointModel())) as c:
        d = c.post(
            "/api/documents", files={"file": ("a.txt", b"Mira reviews the postmortem.")}
        ).json()
        result = ready(c, d["id"])
        assert any("Recovered source-backed" in w for w in result["warnings"])
        graph = c.get("/api/graph").json()
        assert len(graph["edges"]) == 1
        assert any(n["label"] == "postmortem" and n["type"] == "entity" for n in graph["nodes"])


class CrowdedEdgesModel(FakeModel):
    def __init__(self):
        self.prompts = []

    async def generate(self, prompt, schema):
        if "relations" in schema.get("properties", {}):
            relations = [
                {
                    "source": f"Alpha{i}",
                    "target": f"Beta{i}",
                    "label": "connects",
                    "quote": f"Alpha{i} connects Beta{i}.",
                }
                for i in range(15)
            ]
            relations.append(
                {
                    "source": "Mira",
                    "target": "Acme",
                    "label": "leads",
                    "quote": "Mira leads Acme.",
                }
            )
            return {
                "entities": [
                    {"name": "Mira", "type": "person"},
                    {"name": "Acme", "type": "company"},
                ],
                "relations": relations,
            }
        self.prompts.append(prompt)
        return await super().generate(prompt, schema)


def test_candidate_relation_ranking_prefers_query_overlap(tmp_path):
    import json

    model = CrowdedEdgesModel()
    text = (
        " ".join(f"Alpha{i} connects Beta{i}." for i in range(15)) + " Mira leads Acme."
    ).encode()
    with TestClient(create_app(Settings(data_dir=tmp_path), model)) as c:
        d = c.post("/api/documents", files={"file": ("a.txt", text)}).json()
        ready(c, d["id"])
        c.post("/api/chat", json={"question": "Who leads Acme?"})
        edges = json.loads(model.prompts[-1].split("CANDIDATE RELATIONSHIPS:\n")[1])
        assert any(e["label"] == "leads" and e["target_label"] == "Acme" for e in edges)


def test_chunk_boundaries_and_complete_coverage():
    from company_assistant.main import chunks

    text = "".join(f"Mira reviews the postmortem number {i}.\n" for i in range(80))
    pieces = chunks(text)
    assert all(len(p) <= 650 for p in pieces)
    assert all(p.startswith("Mira") for p in pieces)
    assert all(p.endswith("\n") for p in pieces)
    assert all(f"number {i}." in "\n".join(pieces) for i in range(80))
    assert any(pieces[i].splitlines()[-1] in pieces[i + 1] for i in range(len(pieces) - 1))


def test_content_terms_filters_stopwords_and_single_chars():
    from company_assistant.main import content_terms

    terms = content_terms("Who is listed as the Founder of Niruthi?")
    assert "founder" in terms and "niruthi" in terms and "listed" in terms
    assert not (terms & {"who", "is", "as", "the", "of"})


class RankingProbeModel(FakeModel):
    def __init__(self):
        self.last_prompt = ""

    async def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]

    async def generate(self, prompt, schema):
        if "relations" in schema.get("properties", {}):
            return await super().generate(prompt, schema)
        self.last_prompt = prompt
        import re as _re

        ids = _re.findall(r"CHUNK ([0-9a-f-]{36})", prompt)
        return {"answer": "ok", "citation_ids": ids[:1]}


def test_retrieval_prefers_content_overlap_over_stopwords(tmp_path):
    # The ranker previously summed cosine (constant under FakeModel) with
    # stopword-inflated lexical overlap, so a decoy chunk that only matched
    # {as, the, is, of} outranked the chunk literally answering the question.
    # content_terms drops stopwords before scoring; the content chunk now wins.
    model = RankingProbeModel()
    content_doc = b"Mira leads Acme.\nNiruthi lists Founder Dr. Rama Nemani on the homepage.\n"
    decoy_doc = b"It is at the top of the list, as any policy of the file notes.\n" * 2
    with TestClient(create_app(Settings(data_dir=tmp_path), model)) as c:
        content_id = c.post("/api/documents", files={"file": ("content.md", content_doc)}).json()[
            "id"
        ]
        ready(c, content_id)
        decoy_id = c.post("/api/documents", files={"file": ("decoy.md", decoy_doc)}).json()["id"]
        ready(c, decoy_id)
        reply = c.post(
            "/api/chat", json={"question": "Who is listed as the Founder of Niruthi?"}
        ).json()
        assert reply["citations"], "Model should have had a chunk to cite"
        assert reply["citations"][0]["document_name"] == "content.md", (
            "Content chunk must rank above stopword decoy; got "
            f"{reply['citations'][0]['document_name']}"
        )


def test_citation_public_urls_come_from_document_headers(tmp_path):
    text = (
        b"Source: https://niruthi.com/about\nMira leads Acme.\nSource: https://niruthi.com/team\n"
    )
    with TestClient(create_app(Settings(data_dir=tmp_path), FakeModel())) as c:
        d = c.post("/api/documents", files={"file": ("public.txt", text)}).json()
        ready(c, d["id"])
        citation = c.get("/api/graph").json()["edges"][0]["evidence"][0]
        assert citation["source_urls"] == ["https://niruthi.com/about", "https://niruthi.com/team"]
        assert (
            c.get("/api/sources/" + citation["chunk_id"]).json()["source_urls"]
            == citation["source_urls"]
        )
