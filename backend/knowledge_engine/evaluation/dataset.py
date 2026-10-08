"""Corpus + question loading for retrieval evaluation. Domain-agnostic: any folder of text/markdown
documents and any question file with {id, question, type, source_documents, expected_keywords}."""

import json
from dataclasses import dataclass, field
from pathlib import Path

DOC_SUFFIXES = {".md", ".txt"}


@dataclass
class Case:
    id: str
    type: str
    question: str
    docs: set[str]  # gold source document names ("unknown" cases have none)
    keywords: list[str]
    parent: str | None = None  # id of the question this one follows up on
    history: list[str] = field(default_factory=list)  # earlier user questions, oldest first


def load_corpus(paths: list[Path]) -> dict[str, str]:
    """name -> text for every .md/.txt under the given files/folders (non-recursive)."""
    corpus: dict[str, str] = {}
    for path in paths:
        files = [path] if path.is_file() else sorted(path.glob("*"))
        for f in files:
            if f.suffix.lower() in DOC_SUFFIXES and f.is_file():
                corpus[f.name] = f.read_text(encoding="utf-8-sig")
    return corpus


def load_cases(paths: list[Path]) -> list[Case]:
    cases: dict[str, Case] = {}
    for path in paths:
        for q in json.loads(path.read_text()):
            prefix = f"{path.parent.name}/{path.stem}"
            cid = f"{prefix}:{q['id']}"
            parent = f"{prefix}:{q['followup_of']}" if q.get("followup_of") else None
            cases[cid] = Case(
                id=cid,
                type=q.get("type", "single"),
                question=q["question"],
                docs={Path(s).name for s in q.get("source_documents", [])},
                keywords=list(q.get("expected_keywords", [])),
                parent=parent,
            )
    for case in cases.values():  # resolve follow-up chains into the user-question history
        chain: list[str] = []
        parent = case.parent
        while parent and parent in cases:
            chain.insert(0, cases[parent].question)
            parent = cases[parent].parent
        case.history = chain
    return list(cases.values())
