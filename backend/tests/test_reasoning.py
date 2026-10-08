from knowledge_engine.reasoning.contradictions import find_conflicts
from knowledge_engine.reasoning.paths import describe, find_path


def relation(id, s, p, o):
    return {
        "id": id,
        "subject_id": s,
        "predicate": p,
        "object_id": o,
        "subject_name": s.upper(),
        "object_name": o.upper(),
    }


RELS = [
    relation("r1", "priya", "responsible_for", "api"),
    relation("r2", "priya", "reports_to", "anil"),
    relation("r3", "api", "depends_on", "db"),
]


def test_shortest_path_connects_entities_through_relations_in_either_direction():
    path = find_path(RELS, "anil", "db")
    assert [r["id"] for r in path] == ["r2", "r1", "r3"]
    assert describe(
        path,
        {
            "reports_to": "reports to",
            "responsible_for": "is responsible for",
            "depends_on": "depends on",
        },
    ) == ("PRIYA reports to ANIL; PRIYA is responsible for API; API depends on DB")


def test_paths_respect_hop_limit_and_absence_is_not_a_conclusion():
    assert find_path(RELS, "anil", "db", max_hops=2) is None
    assert find_path(RELS, "anil", "nobody") is None
    assert find_path(RELS, "anil", "anil") == []


def test_functional_predicates_with_two_objects_are_a_conflict():
    rels = [*RELS, relation("r4", "priya", "reports_to", "bob")]
    (c,) = find_conflicts(rels, {"reports_to"})
    assert (c.subject, c.predicate, set(c.objects)) == ("PRIYA", "reports_to", {"ANIL", "BOB"})
    assert find_conflicts(rels, set()) == [] and find_conflicts(RELS, {"reports_to"}) == []
