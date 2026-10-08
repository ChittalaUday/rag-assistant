import pytest
from knowledge_engine.extraction.ontology import load_ontology


def test_default_ontology_is_domain_neutral_and_consistent():
    o = load_ontology()
    assert {"Person", "Organization", "Role", "Product", "Concept"} <= set(o.types)
    assert "related_to" in o.predicates
    for p in o.predicates.values():
        assert (p.domain | p.range) <= set(o.types)
    banned = {"niruthi", "company assistant", "fictional"}
    guide = o.prompt_guide().casefold()
    assert not any(word in guide for word in banned)


def test_type_aliases_and_constraints():
    o = load_ontology()
    assert o.entity_type("company").name == "Organization"
    assert o.entity_type("PERSON").name == "Person"
    assert o.entity_type("literal source concept") is None
    leads, has_role = o.predicate("leads"), o.predicate("has_role")
    assert o.allows(leads, "Person", "Project") and not o.allows(leads, "Product", "Person")
    assert o.allows(has_role, "Person", "Role") and not o.allows(has_role, "Role", "Person")
    assert o.predicate("Reports To").name == "reports_to" and o.predicate("owns") is None
    assert o.predicates["reports_to"].functional and o.predicates["collaborates_with"].symmetric


def test_schema_constrains_types_and_predicates_to_closed_enums():
    o = load_ontology()
    schema = o.extraction_schema()
    ent = schema["properties"]["entities"]["items"]["properties"]["type"]["enum"]
    rel = schema["properties"]["relations"]["items"]["properties"]["predicate"]["enum"]
    assert ent == list(o.types) and rel == list(o.predicates)
    assert set(schema["required"]) == {"entities", "relations"}


def test_custom_ontology_is_validated(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text(
        '[types.A]\ndescription="a"\n[predicates.related_to]\nlabel="x"\ndescription="d"\ndomain=["Nope"]\n'
    )
    with pytest.raises(ValueError, match="unknown types"):
        load_ontology(bad)
    missing = tmp_path / "missing.toml"
    missing.write_text('[types.A]\ndescription="a"\n[predicates.p]\nlabel="p"\ndescription="d"\n')
    with pytest.raises(ValueError, match="fallback"):
        load_ontology(missing)
    ok = tmp_path / "ok.toml"
    ok.write_text(
        '[types.Gene]\ndescription="a gene"\nstandalone=true\n[predicates.related_to]\nlabel="r"\ndescription="d"\n'
    )
    assert load_ontology(ok).types["Gene"].standalone
    assert load_ontology(ok).version != load_ontology().version


def test_bundled_example_ontology_for_another_domain_loads_and_drives_the_schema():
    from knowledge_engine.config import REPO_ROOT

    o = load_ontology(REPO_ROOT / "docs" / "ontology-examples" / "software.toml")
    assert {"Person", "Department", "SoftwareService", "Technology"} <= set(o.types)
    assert o.entity_type("api").name == "SoftwareService" and o.predicates["reports_to"].functional
    assert o.allows(o.predicate("depends_on"), "SoftwareService", "Technology")
    assert not o.allows(o.predicate("depends_on"), "Person", "Technology")
    schema = o.extraction_schema()
    assert schema["properties"]["relations"]["items"]["properties"]["predicate"]["enum"] == list(
        o.predicates
    )
    assert (
        "Payment" not in o.prompt_guide()
    )  # nothing from the default domain leaks into the prompt


MINIMAL = (
    '[types.Gene]\ndescription="a gene"\n[predicates.related_to]\nlabel="r"\ndescription="d"\n'
)
GOOD_EXAMPLE = (
    '[[examples]]\npassage = "Gene A binds Gene B."\n'
    'relations = [{ subject = "Gene A", predicate = "related_to", object = "Gene B", '
    'phrase = "binds", quote = "Gene A binds Gene B." }]\n'
    'entities = [{ name = "Gene A", type = "Gene" }, { name = "Gene B", type = "Gene" }]\n'
)

DEFAULT_PREFIX_DIGEST = "6e4c4b5e710da9d6"  # sha256 of the byte-identical pre-UDA-66 prefix


def prefix_of(path):
    from fakes import FakeModel
    from knowledge_engine.extraction.extractor import Extractor

    return Extractor(FakeModel(), load_ontology(path), "m").prefix


def test_default_extraction_prompt_is_pinned(tmp_path):
    """Changing the default prompt invalidates the extraction cache: bump PROMPT_VERSION and this
    digest together (the prefix is rules + ontology + the bundled examples)."""
    import hashlib

    from knowledge_engine.extraction.extractor import PROMPT_VERSION

    digest = hashlib.sha256(prefix_of(None).encode()).hexdigest()[:16]
    assert (PROMPT_VERSION, digest) == (5, DEFAULT_PREFIX_DIGEST)


def test_an_ontology_without_examples_renders_none(tmp_path):
    path = tmp_path / "o.toml"
    path.write_text(MINIMAL)
    assert load_ontology(path).example_guide() == ""
    assert "EXAMPLE" not in prefix_of(path)


def test_examples_are_part_of_the_prompt_not_of_the_ontology_version(tmp_path):
    plain, shown = tmp_path / "a.toml", tmp_path / "b.toml"
    plain.write_text(MINIMAL)
    shown.write_text(MINIMAL + GOOD_EXAMPLE)
    assert load_ontology(plain).version == load_ontology(shown).version
    guide = load_ontology(shown).example_guide()
    assert guide.startswith("EXAMPLE 1 passage:\nGene A binds Gene B.\nEXAMPLE 1 answer:\n")
    assert guide.index('"relations"') < guide.index('"entities"')  # same order as the schema
    assert prefix_of(shown) != prefix_of(plain)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        ('type = "Gene" }, { name = "Gene B"', "unknown entity type"),
        ('predicate = "related_to"', "unknown predicate"),
        ('object = "Gene B"', "is not listed"),
        ('quote = "Gene A binds Gene B."', "is not verbatim"),
        ('phrase = "binds"', "is not inside its quote"),
    ],
)
def test_examples_that_contradict_the_ontology_fail_to_load(tmp_path, edit, message):
    bad = {
        'type = "Gene" }, { name = "Gene B"': GOOD_EXAMPLE.replace(
            'type = "Gene" }, { name = "Gene B"', 'type = "Nope" }, { name = "Gene B"'
        ),
        'predicate = "related_to"': GOOD_EXAMPLE.replace(
            'predicate = "related_to"', 'predicate = "binds_to"'
        ),
        'object = "Gene B"': GOOD_EXAMPLE.replace('object = "Gene B"', 'object = "Gene C"'),
        'quote = "Gene A binds Gene B."': GOOD_EXAMPLE.replace(
            'quote = "Gene A binds Gene B."', 'quote = "Gene A BINDS Gene B."'
        ),
        'phrase = "binds"': GOOD_EXAMPLE.replace('phrase = "binds"', 'phrase = "inhibits"'),
    }[edit]
    path = tmp_path / "o.toml"
    path.write_text(MINIMAL + bad)
    with pytest.raises(ValueError, match=f"Example 1: .*{message}"):
        load_ontology(path)


def test_examples_may_not_violate_domain_and_range(tmp_path):
    path = tmp_path / "o.toml"
    path.write_text(
        MINIMAL.replace('label="r"', 'label="r"\ndomain=["Other"]\nrange=["Other"]').replace(
            "[predicates", '[types.Other]\ndescription="o"\n[predicates', 1
        )
        + GOOD_EXAMPLE
    )
    with pytest.raises(ValueError, match="may not connect Gene to Gene"):
        load_ontology(path)


def test_the_software_ontology_teaches_its_own_example_not_the_defaults():
    from knowledge_engine.config import REPO_ROOT

    guide = load_ontology(
        REPO_ROOT / "docs" / "ontology-examples" / "software.toml"
    ).example_guide()
    assert "Ledger API" in guide and "Payment API" not in guide
    assert "Payment API" in load_ontology().example_guide()
