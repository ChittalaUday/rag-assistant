from types import SimpleNamespace

from knowledge_engine.generation.attribution import attribute, shares_content, split_statements
from knowledge_engine.reasoning.evidence import Source


def source(alias: str, text: str) -> Source:
    return Source(alias, alias.lower(), "doc.md", None, text, [], 1)


def test_split_statements_on_sentences_lists_and_danda():
    text = "Mira leads Acme. She joined in 2021!\n- Beacon\n- Pulse\nमीरा एक्मे का नेतृत्व करती है। वह 2021 में आईं।"
    assert split_statements(text) == [
        "Mira leads Acme.",
        "She joined in 2021!",
        "- Beacon",
        "- Pulse",
        "मीरा एक्मे का नेतृत्व करती है।",
        "वह 2021 में आईं।",
    ]
    assert split_statements("  \n ... \n") == []
    # abbreviations and initials do not end a sentence
    assert split_statements(
        "Dr. Rama Nemani founded it. The entity is Niruthi Pvt. Ltd. in Hyderabad. J. Smith agreed."
    ) == [
        "Dr. Rama Nemani founded it.",
        "The entity is Niruthi Pvt. Ltd. in Hyderabad.",
        "J. Smith agreed.",
    ]


async def test_attribute_keeps_only_given_aliases_and_numbers():
    class Model:
        async def generate(self, prompt, schema):
            assert "1. Mira leads Acme." in prompt and "S2:\nAcme is led by Mira." in prompt
            return {
                "statements": [
                    {"n": 1, "sources": ["S1", "S2", "S1", "S9"]},  # duplicate and unknown alias
                    {"n": 7, "sources": ["S1"]},  # no such statement
                ]
            }

    ctx = SimpleNamespace(model=Model())
    sources = [source("S1", "Mira leads Acme."), source("S2", "Acme is led by Mira.")]
    found = await attribute(ctx, ["Mira leads Acme.", "That is all."], sources)  # type: ignore[arg-type]
    assert found == [["S1", "S2"], []]


async def test_attribute_without_sources_asks_nothing():
    class Model:
        async def generate(self, prompt, schema):
            raise AssertionError("no model call expected")

    assert await attribute(SimpleNamespace(model=Model()), ["A."], []) == [[]]  # type: ignore[arg-type]


async def test_matches_without_shared_content_words_are_dropped():
    class Model:
        async def generate(self, prompt, schema):
            return {"statements": [{"n": 1, "sources": ["S1", "S2"]}]}

    sources = [
        source("S1", "Vikram Sen approves leave of two or more days."),
        source("S2", "FICTIONAL TEST SCENARIO. Snapshot: 2026-10-05. Authored fixture."),
    ]
    found = await attribute(
        SimpleNamespace(model=Model()), ["Vikram Sen approves the leave."], sources
    )  # type: ignore[arg-type]
    assert found == [["S1"]]
    assert shares_content("Acme.", "acme builds things")  # a one-word statement needs one match
    assert not shares_content("Mira leads Acme.", "Acme builds things")


def test_an_answer_in_another_language_is_not_held_to_shared_words():
    passage = "Headcount at the end of Q3 2026 was 148."
    assert shares_content("नॉर्थस्टार में 148 कर्मचारी थे।", passage)
    assert not shares_content("Mira leads Acme.", passage)  # same script, nothing shared
