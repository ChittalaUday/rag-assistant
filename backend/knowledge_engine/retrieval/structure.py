"""Keep tables and lists whole: chunking may cut a table or list in two, and a question about "all"
of its rows needs both halves. When a selected passage ends or begins inside one, the chunk on the
other side of the cut comes along, even though its own words may not match the question."""

import re
from typing import Any

from knowledge_engine.store.chunks import ChunkRepo

_ITEM = re.compile(r"^\s*(?:\||[-*\u2022]\s|\d{1,3}[.)]\s)")  # table row, bullet, numbered item
MAX_EXTRA = 4  # neighbours added per question
REACH = 2  # chunks followed in each direction


def continues(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Does `second` carry on a table or list that `first` stops in the middle of?"""
    a = [line for line in first["text"].splitlines() if line.strip()]
    b = [line for line in second["text"].splitlines() if line.strip()]
    return bool(
        a
        and b
        and first["heading"] == second["heading"]
        and _ITEM.match(a[-1])
        and any(_ITEM.match(line) for line in b[:3])
    )


def structural_neighbours(
    chunks: ChunkRepo, selected: dict[str, dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """For each selected chunk id, the chunks (in reading order) that complete its table or list."""
    extra: dict[str, list[dict[str, Any]]] = {}
    taken = set(selected)
    root = {i: i for i in selected}
    for step in (1, -1):
        frontier = dict(selected)
        for _ in range(REACH):
            if not frontier or len(taken) - len(selected) >= MAX_EXTRA:
                break
            found: dict[str, dict[str, Any]] = {}
            for n in chunks.adjacent(list(frontier), step):
                source = frontier[n["from_id"]]
                first, second = (source, n) if step == 1 else (n, source)
                if len(taken) - len(selected) >= MAX_EXTRA:
                    break
                if n["id"] not in taken and continues(first, second):
                    found[n["id"]] = n
                    taken.add(n["id"])
                    root[n["id"]] = root[source["id"]]
                    extra.setdefault(root[n["id"]], []).append(n)
            frontier = found
    return {k: sorted(v, key=lambda r: r["position"]) for k, v in extra.items()}
