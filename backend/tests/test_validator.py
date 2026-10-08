from knowledge_engine.extraction.ontology import load_ontology
from knowledge_engine.extraction.validator import (
    RawExtraction,
    locate,
    validate_extraction,
)

ONT = load_ontology()


def run(text, entities, relations):
    raw = RawExtraction.model_validate({"entities": entities, "relations": relations})
    return validate_extraction(raw, text, ONT)


def ent(name, type_="Person"):
    return {"name": name, "type": type_}


def rel(s, p, o, quote, phrase=""):
    return {"subject": s, "predicate": p, "object": o, "phrase": phrase, "quote": quote}


def test_valid_relation_keeps_verbatim_quote_with_offsets_and_confidence():
    text = "Intro.\nMira leads Acme. More text."
    out = run(
        text,
        [ent("Mira"), ent("Acme", "Organization")],
        [rel("Mira", "leads", "Acme", "Mira leads Acme.", "leads")],
    )
    (r,) = out.relations
    assert (r.subject, r.predicate, r.object) == ("Mira", "leads", "Acme")
    assert text[r.quote_start : r.quote_end] == r.quote == "Mira leads Acme."
    assert r.confidence == 0.95 and r.phrase == "leads" and not out.warnings


def test_entities_absent_from_the_source_or_outside_the_ontology_are_rejected():
    out = run("Mira leads Acme.", [ent("Mira"), ent("Zed"), ent("Acme", "Spaceship")], [])
    assert [e.name for e in out.entities] == ["Mira"]
    assert any("absent from source" in w for w in out.warnings)
    assert any("outside the ontology" in w for w in out.warnings)


def test_common_nouns_cannot_pose_as_named_entities():
    out = run(
        "The employees follow the policy of Acme.",
        [ent("employees", "Organization"), ent("Acme", "Organization")],
        [],
    )
    assert [e.name for e in out.entities] == ["Acme"]


def test_unlisted_endpoints_unknown_predicates_and_self_loops_are_rejected():
    text = "Mira leads Acme. Mira admires Mira."
    out = run(
        text,
        [ent("Mira"), ent("Acme", "Organization")],
        [
            rel("Mira", "leads", "Nova", "Mira leads Acme."),
            rel("Mira", "owns", "Acme", "Mira leads Acme."),
            rel("Mira", "leads", "Mira", "Mira admires Mira."),
        ],
    )
    assert out.relations == []
    assert len(out.warnings) == 3


def test_reversed_direction_is_flipped_when_only_that_satisfies_the_types():
    out = run(
        "Acme is led by Mira.",
        [ent("Mira"), ent("Acme", "Organization")],
        [rel("Acme", "leads", "Mira", "Acme is led by Mira.")],
    )
    (r,) = out.relations
    assert (r.subject, r.object) == ("Mira", "Acme")


def test_type_constraint_violations_are_rejected():
    out = run(
        "Acme uses Nova.",
        [ent("Acme", "Organization"), ent("Nova", "Product")],
        [rel("Nova", "leads", "Acme", "Acme uses Nova.")],
    )
    assert out.relations == [] and any("type constraints" in w for w in out.warnings)


def test_generic_related_to_only_between_named_entities():
    text = "Mira mentions the budget process and Acme."
    out = run(
        text,
        [ent("Mira"), ent("Acme", "Organization"), ent("budget process", "Process")],
        [
            rel("Mira", "related_to", "budget process", text),
            rel("Mira", "related_to", "Acme", text),
        ],
    )
    assert [r.object for r in out.relations] == ["Acme"]
    assert out.relations[0].confidence < 0.8


def test_quote_must_exist_and_contain_an_endpoint():
    text = "Mira leads Acme. Nova owns Vega."
    ents = [ent("Mira"), ent("Acme", "Organization")]
    assert run(text, ents, [rel("Mira", "leads", "Acme", "Nova owns Vega.")]).relations == []
    assert run(text, ents, [rel("Mira", "leads", "Acme", "Mira heads Acme.")]).relations == []


def test_evidence_grows_within_a_line_to_include_the_subject():
    text = "- **Platform** — Led by Elena Rossi. Owns the API gateway.\n- **Data** — Owns Harbor.\n"
    ents = [
        ent("Platform", "Organization"),
        ent("API gateway", "Product"),
        ent("Harbor", "Product"),
    ]
    out = run(
        text,
        ents,
        [
            rel("Platform", "responsible_for", "API gateway", "Owns the API gateway.", "Owns"),
            rel(
                "Platform", "responsible_for", "Harbor", "Owns Harbor.", "Owns"
            ),  # other bullet: no
        ],
    )
    (r,) = out.relations
    assert r.quote.startswith("Platform") and "API gateway" in r.quote and "\n" not in r.quote
    assert text[r.quote_start : r.quote_end] == r.quote


def test_markdown_and_whitespace_differences_still_locate_a_real_span():
    text = "**Mira**   leads\n**Acme**."
    start, end, exact = locate(text, "Mira leads Acme.")  # type: ignore[misc]
    assert not exact and "Mira" in text[start:end] and text[start:end].endswith("Acme**.")
    assert locate(text, "nothing like it") is None


def test_unsupported_phrase_is_dropped_but_relation_kept_with_lower_confidence():
    text = "Mira leads Acme."
    ents = [ent("Mira"), ent("Acme", "Organization")]
    good = run(text, ents, [rel("Mira", "leads", "Acme", text, "leads")]).relations[0]
    bad = run(text, ents, [rel("Mira", "leads", "Acme", text, "supervises")]).relations[0]
    assert bad.phrase is None and bad.confidence < good.confidence


def test_duplicate_relations_collapse_and_entities_without_relations_follow_standalone_rule():
    text = "Mira leads Acme. The budget process matters."
    out = run(
        text,
        [ent("Mira"), ent("Acme", "Organization"), ent("budget process", "Process")],
        [
            rel("Mira", "leads", "Acme", "Mira leads Acme."),
            rel("Mira", "leads", "Acme", "Mira leads Acme."),
        ],
    )
    assert len(out.relations) == 1
    assert {e.name for e in out.entities} == {
        "Mira",
        "Acme",
    }  # the lone abstract process is dropped
