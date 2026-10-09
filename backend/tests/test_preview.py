"""The live preview finds the passages a question is likely to draw on, without calling a model."""

from fakes import FakeModel, ready
from test_api import client, upload


class Mute(FakeModel):
    """Answers while indexing, then fails loudly: the preview must not need a model."""

    armed = False

    async def embed(self, texts):
        assert not self.armed, "the preview called the embedding model"
        return await super().embed(texts)

    async def generate(self, prompt, schema, max_tokens=None, think=False, model=None):
        assert not self.armed, "the preview called the chat model"
        return await super().generate(prompt, schema, max_tokens)


def post(c, text, **body):
    response = c.post("/api/preview", json={"text": text, **body})
    assert response.status_code == 200, response.text
    return response.json()


ROSTER = "# Roster\n\nArun Ballah reports to Maria Lindqvist and writes the weekly report.\n"
HOLIDAYS = "# Holidays\n\nThe holiday calendar lists public holidays.\n"


def test_typed_text_lists_the_sections_it_is_likely_to_draw_on(tmp_path):
    model = Mute()
    with client(tmp_path, model) as c:
        roster = ready(c, upload(c, "roster.md", ROSTER)["id"])
        ready(c, upload(c, "holidays.md", HOLIDAYS)["id"])
        model.armed = True
        found = post(c, "who does arun report to")
        assert [p["document"] for p in found["passages"]] == ["roster.md"]
        (passage,) = found["passages"]
        assert passage["document_id"] == roster["id"] and passage["heading"] == "Roster"
        assert post(c, "which holidays are public")["passages"][0]["document"] == "holidays.md"
        assert post(c, "the weather on mars") == {
            "passages": [],
            "corrections": {},
            "graph": {"entities": [], "relations": []},
        }


def test_typed_names_are_recognised_and_related_by_the_sentence_that_links_them(tmp_path):
    model = Mute()
    with client(tmp_path, model) as c:
        ready(c, upload(c, "roster.md", ROSTER + "- Arun joined Northwind in 2020.\n")["id"])
        model.armed = True
        found = post(c, "who does Arun report to, and Zed Corp, and Maria Lindqvist")["graph"]
        entities = {e["id"]: (e["typed"], e["found"]) for e in found["entities"]}
        assert entities == {
            "arun ballah": (True, True),  # "Arun" folds into the one full name containing it
            "maria lindqvist": (True, True),
            "northwind": (False, True),
            "zed corp": (True, False),  # typed, named in no passage
        }
        relations = {(r["source"], r["label"], r["target"]) for r in found["relations"]}
        assert relations == {
            ("arun ballah", "reports to", "maria lindqvist"),
            ("arun ballah", "joined", "northwind"),
        }
        quote = next(r["quote"] for r in found["relations"] if r["label"] == "reports to")
        assert quote.startswith("Arun Ballah reports to Maria Lindqvist")


def test_misspellings_are_fixed_except_the_word_still_being_typed(tmp_path):
    model = Mute()
    with client(tmp_path, model) as c:
        ready(c, upload(c, "roster.md", ROSTER)["id"])
        model.armed = True
        found = post(c, "who does arun reprot to")  # swapped letters
        assert found["corrections"] == {"reprot": "report"}
        assert found["passages"][0]["document"] == "roster.md"
        assert post(c, "weekly repor")["corrections"] == {}  # still typing "repor..."


def test_the_preview_is_bounded_respects_labels_and_survives_odd_text(tmp_path):
    with client(tmp_path) as c:
        for i in range(8):
            ready(c, upload(c, f"r{i}.md", f"# Part {i}\n\nThe roster section {i}.\n")["id"])
        ready(
            c,
            upload(c, "fiction.md", "# Roster\n\nAn invented roster.\n", labels="synthetic")["id"],
        )
        assert len(post(c, "roster")["passages"]) == 5
        hidden = post(c, "invented roster", exclude_labels=["synthetic"])["passages"]
        assert "fiction.md" not in {p["document"] for p in hidden}
        for text in ("", "   ", "🙂" * 500, "a" * 2000, "'; DROP TABLE chunks; --"):
            assert set(post(c, text)) == {"passages", "corrections", "graph"}
        assert c.post("/api/preview", json={"text": "a\x00b"}).status_code == 422
        assert c.post("/api/preview", json={"text": "a" * 2001}).status_code == 422


def test_the_full_graph_holds_every_relation_and_respects_labels(tmp_path):
    model = Mute()
    with client(tmp_path, model) as c:
        ready(c, upload(c, "roster.md", ROSTER)["id"])
        ready(
            c,
            upload(c, "fiction.md", "# F\n\nZara Quill mentors Omar Vance.\n", labels="synthetic")[
                "id"
            ],
        )
        model.armed = True
        response = c.post("/api/graph", json={})
        assert response.status_code == 200, response.text
        relations = {(r["source"], r["label"], r["target"]) for r in response.json()["relations"]}
        assert relations == {
            ("arun ballah", "reports to", "maria lindqvist"),
            ("zara quill", "mentors", "omar vance"),
        }
        hidden = c.post("/api/graph", json={"exclude_labels": ["synthetic"]}).json()
        assert {e["id"] for e in hidden["entities"]} == {"arun ballah", "maria lindqvist"}


def test_each_relation_carries_the_script_of_its_passage(tmp_path):
    model = Mute()
    with client(tmp_path, model) as c:
        ready(c, upload(c, "roster.md", ROSTER)["id"])
        te = (
            "# జాబితా\n\nఅరుణ్ బల్లా (Arun Ballah) ప్రతి వారం తన నివేదికను మరియా (Maria Lindqvist)"
            " కు పంపుతారు. ఈ బృందం సంస్థ యొక్క వార్షిక ప్రణాళికను సిద్ధం చేస్తుంది.\n"
        )
        ready(c, upload(c, "roster-te.md", te)["id"])
        model.armed = True
        relations = c.post("/api/graph", json={}).json()["relations"]
        scripts = {r["document"]: r["script"] for r in relations}
        assert scripts == {"roster.md": "LATIN", "roster-te.md": "TELUGU"}
