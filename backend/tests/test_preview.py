"""The live preview names entities and relations from question text without calling a model."""

from fakes import FakeModel, ready
from test_api import client, upload


class Mute(FakeModel):
    """Answers while indexing, then fails loudly: the preview must not need a model."""

    armed = False

    async def embed(self, texts):
        assert not self.armed, "the preview called the embedding model"
        return await super().embed(texts)

    async def generate(self, prompt, schema, max_tokens=None):
        assert not self.armed, "the preview called the chat model"
        return await super().generate(prompt, schema, max_tokens)


def post(c, text):
    response = c.post("/api/preview", json={"text": text})
    assert response.status_code == 200, response.text
    return response.json()


def test_typed_text_selects_the_entities_it_names_and_the_relation_it_asks_about(tmp_path):
    model = Mute()
    with client(tmp_path, model) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        edge = c.get("/api/graph").json()["edges"][0]
        model.armed = True
        result = post(c, "who leads acme")
        assert [e["name"] for e in result["entities"]] == ["Acme"]
        assert result["highlighted_edge_ids"] == [edge["id"]]  # "leads" bears on the question
        assert set(result["highlighted_node_ids"]) == {edge["source"], edge["target"]}
        both = post(c, "Mira and Acme")
        assert {e["name"] for e in both["entities"]} == {"Mira", "Acme"}
        assert both["highlighted_edge_ids"] == [edge["id"]]  # both ends are named


def test_text_without_known_names_or_with_unrelated_words_selects_little(tmp_path):
    with client(tmp_path) as c:
        ready(c, upload(c, "a.txt", "Mira leads Acme.")["id"])
        empty = {"entities": [], "highlighted_node_ids": [], "highlighted_edge_ids": []}
        assert post(c, "") == empty and post(c, "what is the weather today") == empty
        named_only = post(c, "tell me about acme")  # a name, but no relation word
        assert [e["name"] for e in named_only["entities"]] == ["Acme"]
        assert named_only["highlighted_edge_ids"] == []
        assert named_only["highlighted_node_ids"] == [named_only["entities"][0]["id"]]


def test_the_preview_is_bounded_and_survives_odd_text(tmp_path):
    with client(tmp_path) as c:
        assert post(c, "a\x00b \ud7ff")["entities"] == []  # never reaches the database as text
        assert c.post("/api/preview", json={"text": "x" * 2001}).status_code == 422


class Roster(FakeModel):
    """Finds the people and the one-word company in the roster, and the team's reporting line."""

    def extract(self, prompt):
        if "Policy Board" in prompt:
            return {"entities": [{"name": "Policy Board", "type": "Organization"}], "relations": []}
        if "Lindqvist" not in prompt:
            return {"entities": [], "relations": []}
        return {
            "entities": [
                {"name": "Maria Lindqvist", "type": "Person"},
                {"name": "Arun Ballah", "type": "Person"},
                {"name": "Acme", "type": "Organization"},
            ],
            "relations": [
                {
                    "subject": "Arun Ballah",
                    "predicate": "reports_to",
                    "object": "Maria Lindqvist",
                    "phrase": "reports to",
                    "quote": "Arun Ballah reports to Maria Lindqvist.",
                },
                {
                    "subject": "Maria Lindqvist",
                    "predicate": "leads",
                    "object": "Acme",
                    "phrase": "leads",
                    "quote": "Maria Lindqvist leads Acme.",
                },
            ],
        }


def test_the_live_highlight_survives_misspellings_first_names_and_half_typed_words(tmp_path):
    with client(tmp_path, Roster()) as c:
        ready(
            c,
            upload(
                c,
                "roster.md",
                "Arun Ballah reports to Maria Lindqvist. Maria Lindqvist leads Acme.",
            )["id"],
        )
        for i in range(5):  # "policy" is an everyday word in this corpus, and also part of a name
            ready(c, upload(c, f"p{i}.md", f"Leave policy number {i} applies to everyone.")["id"])
        ready(c, upload(c, "board.md", "The Policy Board meets monthly.")["id"])
        graph = c.get("/api/graph").json()
        reports = next(e["id"] for e in graph["edges"] if e["predicate"] == "reports_to")
        leads = next(e["id"] for e in graph["edges"] if e["predicate"] == "leads")
        cases = {
            "who does arun ballah report to": ({"Arun Ballah"}, {reports}),
            "who does arun balla reprts to": ({"Arun Ballah"}, {reports}),  # name and verb misspelt
            "who does Arun reports to": ({"Arun Ballah"}, {reports}),  # first name alone
            "who does Ballah report to": ({"Arun Ballah"}, {reports}),  # last name alone
            "who leads acmee": ({"Acme"}, {leads}),  # one-word name misspelt
            "what does lindqvst lead": ({"Maria Lindqvist"}, {leads}),  # misspelt last name
            "who does arun ballah repo": (
                {"Arun Ballah"},
                set(),
            ),  # verb still being typed: no relation yet
            "what is the policy": (set(), set()),  # an everyday word is not the board's name
        }
        for text, (names, edges) in cases.items():
            result = post(c, text)
            assert {e["name"] for e in result["entities"]} == names, text
            assert set(result["highlighted_edge_ids"]) == edges, text
