from knowledge_engine.generation.prompt import build_prompt
from knowledge_engine.reasoning.evidence import Evidence, Fact, Source
from knowledge_engine.retrieval.models import QueryPlan


def source(n, text, rank):
    return Source(f"S{n}", f"c{n}", f"doc{n}.md", None, text, [], rank)


def fact(rid, aliases):
    return Fact(rid, "Mira", "leads", "leads", "Acme", "Mira leads Acme.", aliases, "m", "a", 0.9)


def plan(question="Who leads Acme?"):
    return QueryPlan(question, question, False, [], [], "lookup", 5, 1)


def test_prompt_shrinks_by_dropping_connections_then_facts_then_the_weakest_passage():
    big = "word " * 9000  # ~45 KB: cannot fit alongside the other passage
    ev = Evidence(
        sources=[source(1, "Mira leads Acme.", 1), source(2, big, 2)],
        facts=[fact("r1", ["S1"]), fact("r2", ["S2"])],
        connections=["A; B"],
    )
    prompt, schema = build_prompt(plan(), [], ev)
    assert "CHUNK S2" not in prompt and "CHUNK S1" in prompt
    assert schema["properties"]["citation_ids"]["items"]["enum"] == ["S1"]
    assert ev.connections == [] and ev.facts == [] and [s.alias for s in ev.sources] == ["S1"]


def test_a_small_overflow_costs_only_connections_and_facts_not_passages():
    from copy import deepcopy

    from knowledge_engine.llm.ollama import prompt_budget_bytes

    def evidence():
        return Evidence(
            sources=[source(1, "Mira leads Acme. " * 600, 1)],  # a ~10 KB passage
            facts=[fact(f"r{i}", ["S1"]) for i in range(5)],
            connections=["Mira leads Acme; Acme uses Nova"],
        )

    base, schema = build_prompt(plan(), [], deepcopy(evidence()))
    extra = "x" * (prompt_budget_bytes(schema) - len(base.encode()) + 60)  # 60 bytes over budget
    ev = evidence()
    prompt, _ = build_prompt(plan(), [], ev, extra=extra)
    assert len(prompt.encode()) <= prompt_budget_bytes(schema)
    assert len(ev.sources) == 1 and "CHUNK S1" in prompt  # the passage survived
    assert ev.connections == [] and len(ev.facts) == 4  # connections first, then one fact


def test_question_is_never_cut_and_oversized_questions_are_refused():
    import pytest
    from knowledge_engine.errors import AppError

    ev = Evidence(sources=[source(1, "Mira leads Acme.", 1)])
    q = "Who leads Acme? " * 100
    assert q in build_prompt(plan(q), [], ev)[0]
    with pytest.raises(AppError) as err:
        build_prompt(plan("x" * 30000), [], Evidence(sources=[source(1, "t", 1)]))
    assert err.value.status_code == 413
