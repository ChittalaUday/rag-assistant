import json

from knowledge_engine.generation.prompt import build_prompt
from knowledge_engine.reasoning.evidence import Evidence, Source
from knowledge_engine.retrieval.models import QueryPlan


def source(n, text, rank):
    return Source(f"S{n}", f"c{n}", f"doc{n}.md", None, text, [], rank)


def plan(question="Who leads Acme?"):
    return QueryPlan(question, question, False, [], "lookup", 5)


def test_prompt_shrinks_by_dropping_the_weakest_passage():
    big = "word " * 9000  # ~45 KB: cannot fit alongside the other passage
    ev = Evidence(sources=[source(1, "Mira leads Acme.", 1), source(2, big, 2)])
    prompt, schema = build_prompt(plan(), [], ev)
    assert "CHUNK S2" not in prompt and "CHUNK S1" in prompt
    assert schema["properties"]["citation_ids"]["items"]["enum"] == ["S1"]
    assert [s.alias for s in ev.sources] == ["S1"]


def test_thinking_leaves_room_for_reasoning_tokens_in_the_context():
    from knowledge_engine.llm.ollama import NUM_PREDICT, THINKING_NUM_PREDICT, prompt_budget_bytes

    passage = "Mira leads Acme. " * 1300  # ~22 KB: fits the plain budget, not the thinking one
    schema = build_prompt(plan(), [], Evidence(sources=[source(1, "x", 1)]))[1]
    assert (
        prompt_budget_bytes(schema, THINKING_NUM_PREDICT)
        < len(passage)
        < prompt_budget_bytes(schema, NUM_PREDICT)
    )
    two = lambda: Evidence(sources=[source(1, "Mira.", 1), source(2, passage, 2)])  # noqa: E731
    assert "CHUNK S2" in build_prompt(plan(), [], two())[0]
    assert "CHUNK S2" not in build_prompt(plan(), [], two(), think=True)[0]


def test_question_is_never_cut_and_oversized_questions_are_refused():
    import pytest
    from knowledge_engine.errors import AppError

    ev = Evidence(sources=[source(1, "Mira leads Acme.", 1)])
    q = "Who leads Acme? " * 100
    assert q in build_prompt(plan(q), [], ev)[0]
    with pytest.raises(AppError) as err:
        build_prompt(plan("x" * 30000), [], Evidence(sources=[source(1, "t", 1)]))
    assert err.value.status_code == 413


def test_the_answer_being_generated_is_read_from_the_partial_json():
    from knowledge_engine.generation.answer import draft_answer  # noqa: PLC0415

    full = json.dumps({"answer": 'Mira "leads"\nAcme — 🙂', "citation_ids": ["S1"]})
    assert draft_answer(full) == 'Mira "leads"\nAcme — 🙂'
    assert draft_answer("") == draft_answer('{"answ') == draft_answer('{"answer": ') == ""
    assert draft_answer('{"answer": "Mira le') == "Mira le"
    assert draft_answer('{"answer": "Mira \\') == "Mira "  # escape cut off
    assert draft_answer('{"answer": "caf\\u00') == "caf"  # unicode escape cut off
    assert draft_answer('{"answer": "a\\nb') == "a\nb"
