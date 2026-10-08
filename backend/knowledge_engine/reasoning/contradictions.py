"""Conflict detection. A predicate marked functional in the ontology (e.g. reports_to) has one
object per subject; two different objects with evidence are surfaced, never silently resolved."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Conflict:
    subject: str
    predicate: str
    objects: tuple[str, ...]
    relation_ids: tuple[str, ...]


def find_conflicts(relations: list[dict[str, Any]], functional: set[str]) -> list[Conflict]:
    grouped: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for r in relations:
        if r["predicate"] in functional:
            grouped.setdefault((r["subject_id"], r["predicate"]), {})[r["object_id"]] = r
    out: list[Conflict] = []
    for (_, predicate), objects in sorted(grouped.items()):
        if len(objects) > 1:
            rows = [objects[k] for k in sorted(objects)]
            out.append(
                Conflict(
                    subject=rows[0]["subject_name"],
                    predicate=predicate,
                    objects=tuple(sorted(r["object_name"] for r in rows)),
                    relation_ids=tuple(r["id"] for r in rows),
                )
            )
    return out
