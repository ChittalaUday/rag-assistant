"""End-to-end answer evaluation, in process: index a corpus in a scratch schema, ask every question
through the real query pipeline (the same `chat` the API calls) and score the answers.

Scores are keyword and citation based, so they reward the sources' wording and cannot judge
paraphrase; they are smoke metrics for comparing configurations, not a measure of truth.
"""

import re
import time
import unicodedata
from collections import defaultdict
from typing import Any

from knowledge_engine.context import AppContext
from knowledge_engine.evaluation.dataset import Case
from knowledge_engine.pipeline.querying import chat
from knowledge_engine.schemas.chat import Question

UNANSWERABLE = {"unknown", "unanswerable", "abstention"}
_ABSTAINED = re.compile(
    r"\b(not (?:explicitly |directly )?(?:provided|available|specified|documented|found|"
    r"established|disclosed|mentioned|stated|given|listed|included|named|identified)|"
    r"(?:do|does|is) not (?:contain|mention|specify|provide|establish|disclose|state|say|list)|"
    r"(?:don.t|cannot|can.t|could not) (?:find|determine|answer)|"
    r"no (?:supporting|relevant|evidence|information|public source)|"
    r"insufficient (?:evidence|information))\b",
    re.IGNORECASE,
)


def comparable(text: str) -> str:
    """For keyword matching across scripts: NFC, case-folded, any script's digits as 0-9, digit
    grouping removed ("২১৫" == "215", "25000" == "25,000" == "2,00,000"-style lakh grouping)."""
    text = unicodedata.normalize("NFC", text).casefold()
    text = "".join(
        str(unicodedata.decimal(c)) if c.isdecimal() and not c.isascii() else c for c in text
    )
    return re.sub(r"(?<=\d)[,\u066c](?=\d)", "", text)


def abstained(answer: str, status: str) -> bool:
    return status == "insufficient_evidence" or bool(_ABSTAINED.search(answer))


def score(
    case: Case, answer: str, status: str, cited: set[str], retrieved: set[str]
) -> dict[str, Any]:
    """correct: an unanswerable question is declined; otherwise every expected keyword is in the
    answer and at least one expected document is cited. strict: also every expected document."""
    text = comparable(answer)
    missing = [k for k in case.keywords if comparable(k) not in text]
    declined = abstained(answer, status)
    if case.type in UNANSWERABLE:
        correct = strict = declined
    else:
        correct = not missing and bool(case.docs & cited)
        strict = correct and case.docs <= cited
    return {
        "correct": correct,
        "strict": strict,
        "kw_coverage": 1 - len(missing) / len(case.keywords) if case.keywords else 1.0,
        "source_recall": len(case.docs & cited) / len(case.docs) if case.docs else 1.0,
        "retrieved_recall": len(case.docs & retrieved) / len(case.docs) if case.docs else 1.0,
        "declined": declined,
        "missing_keywords": missing,
        "missing_sources": sorted(case.docs - cited),
    }


async def run_cases(ctx: AppContext, cases: list[Case], verbose: bool = False) -> dict[str, Any]:
    conversations: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    for n, case in enumerate(cases, 1):
        start = time.perf_counter()
        parent = conversations.get(case.parent) if case.parent else None
        result = await chat(ctx, Question(question=case.question, conversation_id=parent))
        seconds = time.perf_counter() - start
        conversations[case.id] = result["conversation_id"]
        cited = {c["document_name"] for c in result["citations"]}
        retrieved = {p["document"] for p in result["trace"]["passages"]}
        row = {
            "id": case.id,
            "type": case.type,
            "question": case.question,
            "answer": result["answer"],
            "status": result["status"],
            "seconds": round(seconds, 2),
            "cited": sorted(cited),
            "retrieved": sorted(retrieved),
            "unsupported": [x["text"] for x in result["statements"] if not x["sources"]],
            **score(case, result["answer"], result["status"], cited, retrieved),
        }
        rows.append(row)
        if verbose:
            mark = "ok " if row["correct"] else "BAD"
            print(f"{n:3}/{len(cases)} {mark} {case.id} ({seconds:.1f}s)", flush=True)
    return {"summary": summarize(rows), "results": rows}


def rescore(rows: list[dict[str, Any]], cases: list[Case]) -> dict[str, Any]:
    """Score saved answers again (after a change to the questions or the scoring). Rows saved
    before cited/retrieved documents were recorded are rebuilt from the recall they stored."""
    by_id = {c.id: c for c in cases}
    out = []
    for row in rows:
        case = by_id[row["id"]]
        cited = set(row.get("cited", case.docs - set(row.get("missing_sources", []))))
        retrieved = set(row.get("retrieved", case.docs if row.get("retrieved_recall") else []))
        out.append({**row, **score(case, row["answer"], row["status"], cited, retrieved)})
    return {"summary": summarize(out), "results": out}


def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups["overall"].append(r)
        groups[r["type"]].append(r)
    keys = ["correct", "strict", "kw_coverage", "source_recall", "retrieved_recall", "seconds"]
    for r in rows:  # share of answers with a sentence no passage states (needs the statements)
        r["flagged"] = float(bool(r.get("unsupported")))
    keys.append("flagged")
    return {
        name: {"n": len(rs), **{k: round(sum(float(r[k]) for r in rs) / len(rs), 3) for k in keys}}
        for name, rs in groups.items()
    }
