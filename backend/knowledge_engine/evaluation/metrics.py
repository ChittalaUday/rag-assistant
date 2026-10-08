"""Retrieval metrics. Chunker-independent where possible: keyword coverage asks whether the
retrieved text actually contains the facts the answer needs."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Hit:
    doc: str
    text: str


def evaluate_hits(hits: list[Hit], docs: set[str], keywords: list[str]) -> dict[str, float]:
    kws = [k.casefold() for k in keywords]
    joined = "\n".join(h.text for h in hits).casefold()
    found = [k for k in kws if k in joined]

    def relevant(h: Hit) -> bool:
        text = h.text.casefold()
        return h.doc in docs and (not kws or any(k in text for k in kws))

    first = next((i for i, h in enumerate(hits, 1) if relevant(h)), 0)
    return {
        "doc_recall": len({h.doc for h in hits} & docs) / len(docs) if docs else 1.0,
        "kw_coverage": len(found) / len(kws) if kws else 1.0,
        "kw_full": float(len(found) == len(kws)),
        "hit": float(first > 0),
        "mrr": 1 / first if first else 0.0,
    }


def summarize(rows: list[dict[str, float]]) -> dict[str, float]:
    return {k: round(sum(r[k] for r in rows) / len(rows), 3) for k in rows[0]} if rows else {}
