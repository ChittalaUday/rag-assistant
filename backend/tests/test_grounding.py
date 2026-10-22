from fakes import FakeModel
from knowledge_engine.generation.grounding import checkable, remove_statements
from knowledge_engine.text import strip_images
from test_api import client, ready, upload


def test_image_file_names_are_not_content_but_alt_text_stays():
    text = "![](img/team/Dr_Rama.png)\n\n#### Dr. Rama Nemani\n\n![Niruthi logo](./logo.png) Founder <img src='a.png'>"
    cleaned = strip_images(text)
    assert "Dr_Rama" not in cleaned and ".png" not in cleaned
    assert "Niruthi logo" in cleaned and "Dr. Rama Nemani" in cleaned


def test_only_sentences_that_state_something_are_checked():
    assert not checkable("### Founder") and not checkable("| --- | --- |")
    assert not checkable("| Dr. Ramakrishna Nemani | Founder | Retired NASA scientist |")
    assert not checkable("**Sector-Specific Services**") and not checkable("- **Late Arrival:**")
    assert not checkable("Niruthi provides services to these sectors:")
    assert checkable("Mira leads the Acme team.") and checkable("- **Late**: grace of 30 minutes")


def test_removing_sentences_keeps_the_rest_and_drops_empty_list_items():
    text = "### Team\n\nMira leads Acme. She is his wife.\n\n- Ravi joined in 2020\n- Ravi is Mira's cousin"
    out = remove_statements(text, ["She is his wife.", "- Ravi is Mira's cousin"])
    assert out == "### Team\n\nMira leads Acme.\n\n- Ravi joined in 2020"


class InventingModel(FakeModel):
    unsupported = ("wife",)

    def answer(self, prompt):
        ids = [i for i in ("S1",) if f"CHUNK {i}" in prompt]
        return {
            "answer": "### Acme\n\nMira leads Acme. She is the wife of Ravi.",
            "citation_ids": ids,
            "status": "answered",
        }


def test_an_answer_sentence_the_passages_do_not_state_is_removed_and_reported(tmp_path):
    with client(tmp_path, InventingModel()) as c:
        doc = upload(c, "a.txt", "Mira leads Acme.")
        ready(c, doc["id"])
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        assert "wife" not in reply["answer"] and "Mira leads Acme." in reply["answer"]
        assert any("Removed 1 statement" in w and "wife" in w for w in reply["warnings"])


class RevisingModel(InventingModel):
    def answer(self, prompt):
        value = super().answer(prompt)
        if "REVISION:" in prompt:  # the rewrite leaves out the unsupported sentence
            value["answer"] = "### Acme\n\nMira leads Acme."
        return value


def test_a_rewrite_without_the_unsupported_sentence_replaces_the_answer(tmp_path):
    with client(tmp_path, RevisingModel()) as c:
        doc = upload(c, "a.txt", "Mira leads Acme.")
        ready(c, doc["id"])
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        assert reply["answer"] == "### Acme\n\nMira leads Acme."
        assert not any("Removed" in w for w in reply["warnings"])
        assert reply["metrics"]["retries"] == ["unsupported_statements"]


def test_an_answer_with_nothing_supported_says_so(tmp_path):
    class AllInvented(InventingModel):
        def answer(self, prompt):
            return {**super().answer(prompt), "answer": "Zed is the wife of Ravi."}

    with client(tmp_path, AllInvented()) as c:
        doc = upload(c, "a.txt", "Mira leads Acme.")
        ready(c, doc["id"])
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        assert reply["status"] == "insufficient_evidence" and "wife" not in reply["answer"]


def test_sentences_made_of_words_the_passages_use_are_not_sent_to_the_judge(tmp_path):
    class Counting(InventingModel):
        judged = 0

        async def generate(self, prompt, schema, **kw):
            if "unsupported" in schema.get("properties", {}):
                Counting.judged += 1
            return await super().generate(prompt, schema, **kw)

    with client(tmp_path, Counting()) as c:
        doc = upload(c, "a.txt", "Mira leads Acme.")
        ready(c, doc["id"])
        c.post("/api/chat", json={"question": "Who leads Acme?"})
        assert (
            Counting.judged == 4
        )  # the invented sentence: screened, then confirmed, before and after the rewrite


def test_citation_tags_and_list_markup_are_not_part_of_a_claim():
    from knowledge_engine.generation.grounding import cited_aliases, claim_text

    assert claim_text("- **Late**: grace of 30 minutes [S6, S7].") == "Late: grace of 30 minutes ."
    assert cited_aliases("A fact [S1, S5] and another (S3).") == ["S1", "S5", "S3"]
    assert not checkable("- Market information [S4].") and checkable(
        "- Employees must submit the form [S2]."
    )


def test_tagged_sentences_skip_the_attribution_call(tmp_path):
    class Counting(FakeModel):
        attributions = 0

        def answer(self, prompt):
            return {
                "answer": "### Acme\n\nMira leads Acme [S1].",
                "citation_ids": ["S1"],
                "status": "answered",
            }

        async def generate(self, prompt, schema, **kw):
            if "maxItems" in schema["properties"].get("statements", {}):
                Counting.attributions += 1
            return await super().generate(prompt, schema, **kw)

    with client(tmp_path, Counting()) as c:
        doc = upload(c, "a.txt", "Mira leads Acme.")
        ready(c, doc["id"])
        reply = c.post("/api/chat", json={"question": "Who leads Acme?"}).json()
        assert Counting.attributions == 0
        assert [s["sources"] != [] for s in reply["statements"]] == [False, True]
