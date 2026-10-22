"""Evidence building: turn retrieved chunks into the citable passages an answer is built from."""

from dataclasses import dataclass, field
from typing import Any

from knowledge_engine.context import AppContext
from knowledge_engine.retrieval.search import Retrieval
from knowledge_engine.text import strip_images


@dataclass
class Source:
    alias: str  # S1, S2 ... short ids the model cites
    chunk_id: str
    document_name: str
    heading: str | None
    text: str  # what the model sees (overlap with the previous passage trimmed)
    labels: list[str]
    rank: int  # retrieval rank, 1 = best


@dataclass
class Evidence:
    sources: list[Source] = field(default_factory=list)


def build_evidence(ctx: AppContext, retrieval: Retrieval) -> Evidence:
    ev = Evidence()
    # keep the best-ranked passages that fit the budget, then show them in reading order
    chosen: list[tuple[int, dict[str, Any]]] = []
    used = 0
    for rank, cand in enumerate(retrieval.selected, 1):
        row = retrieval.chunks[cand.chunk_id]
        size = len(row["text"]) + len(row["document_name"]) + 40
        if chosen and used + size > ctx.settings.context_chars:
            continue
        chosen.append((rank, row))
        used += size
    first_seen = {r["document_id"]: i for i, (_, r) in reversed(list(enumerate(chosen)))}
    chosen.sort(key=lambda t: (first_seen[t[1]["document_id"]], t[1]["position"]))
    previous: dict[str, Any] | None = None
    for n, (rank, row) in enumerate(chosen, 1):
        text = row["text"]
        if (
            previous
            and previous["document_id"] == row["document_id"]
            and previous["char_end"] is not None
            and row["char_start"] is not None
            and 0 < previous["char_end"] - row["char_start"] < len(text)
        ):
            text = text[previous["char_end"] - row["char_start"] :]  # overlap already shown
        ev.sources.append(
            Source(
                f"S{n}",
                row["id"],
                row["document_name"],
                row["heading"],
                strip_images(text),
                list(row["labels"]),
                rank,
            )
        )
        previous = row

    return ev
