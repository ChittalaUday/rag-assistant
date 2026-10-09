import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "evaluation", Path(__file__).parents[1] / "evaluate.py"
)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_quote_must_be_in_actual_source():
    result = {
        "answer": "Mira leads Atlas",
        "citations": [
            {
                "chunk_id": "a",
                "document_name": "team.md",
                "text": "Mira leads Atlas.",
                "quote": "Mira owns all budgets.",
            }
        ],
        "statements": [],
    }
    score = module.assess({"expected_keywords": ["Mira"], "source_documents": ["team.md"]}, result)
    assert score["invalid_evidence"]
    assert not score["smoke_pass"]


def test_cross_document_question_requires_both_expected_sources():
    result = {
        "answer": "Mira leads Atlas",
        "citations": [
            {
                "chunk_id": "a",
                "document_name": "team.md",
                "text": "Mira leads Atlas.",
                "quote": "Mira leads Atlas.",
            }
        ],
        "statements": [],
    }
    score = module.assess(
        {"expected_keywords": ["Mira"], "source_documents": ["team.md", "policy.md"]}, result
    )
    assert score["keyword_coverage"] == 1
    assert score["source_coverage"] == 0.5
    assert not score["smoke_pass"]


def test_unknown_question_does_not_pass_with_invented_answer():
    result = {
        "answer": "The CEO is Edwin.",
        "citations": [],
        "statements": [],
    }
    assert not module.assess(
        {"type": "unknown", "expected_keywords": [], "source_documents": []}, result
    )["smoke_pass"]
    result["answer"] = "The documents do not contain the CEO's name."
    assert module.assess(
        {"type": "unknown", "expected_keywords": [], "source_documents": []}, result
    )["smoke_pass"]


def test_actual_api_abstention_counts_as_unknown_success():
    result = {
        "answer": "I could not find supporting evidence in the uploaded documents.",
        "citations": [],
        "statements": [],
    }
    assert module.assess(
        {"type": "unknown", "expected_keywords": [], "source_documents": []}, result
    )["smoke_pass"]


def test_not_established_phrasing_counts_as_abstention():
    # The local model phrases abstentions as "not established by the reviewed
    # public sources"; the scorer must recognize this synonym so correct
    # unknown-type answers do not get wrongly flagged for review.
    base = {"type": "unknown", "expected_keywords": [], "source_documents": []}
    for phrase in [
        "Niruthi's current headcount is not established by the reviewed public sources.",
        "The requested figure is not disclosed in the public sources collected here.",
        "No public source in this collection answers the question.",
    ]:
        assert module.assess(base, {"answer": phrase, "citations": [], "statements": []})[
            "smoke_pass"
        ], phrase
