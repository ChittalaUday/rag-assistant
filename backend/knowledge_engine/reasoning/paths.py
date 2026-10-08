"""Explicit multi-hop reasoning over extracted relations. Paths are shown, never concluded:
a chain A->B->C does not make A related to C unless the ontology says so."""

from collections import deque
from typing import Any

MAX_HOPS = 3


def find_path(
    relations: list[dict[str, Any]], start: str, goal: str, max_hops: int = MAX_HOPS
) -> list[dict[str, Any]] | None:
    """Shortest chain of relations (either direction) connecting two entities, or None."""
    adjacent: dict[str, list[dict[str, Any]]] = {}
    for r in relations:
        adjacent.setdefault(r["subject_id"], []).append(r)
        adjacent.setdefault(r["object_id"], []).append(r)
    queue: deque[tuple[str, list[dict[str, Any]]]] = deque([(start, [])])
    seen = {start}
    while queue:
        node, path = queue.popleft()
        if node == goal:
            return path
        if len(path) == max_hops:
            continue
        for r in adjacent.get(node, []):
            nxt = r["object_id"] if r["subject_id"] == node else r["subject_id"]
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, [*path, r]))
    return None


def describe(path: list[dict[str, Any]], labels: dict[str, str]) -> str:
    """'A leads B; B reports to C' using each relation's own subject/predicate/object order."""
    return "; ".join(
        f"{r['subject_name']} {labels.get(r['predicate'], r['predicate'])} {r['object_name']}"
        for r in path
    )
