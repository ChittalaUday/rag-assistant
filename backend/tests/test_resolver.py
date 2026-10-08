from knowledge_engine.extraction.ontology import load_ontology
from knowledge_engine.extraction.resolver import entity_id, resolve, scan_mentions
from knowledge_engine.extraction.validator import Mention
from knowledge_engine.text import name_key

ONT = load_ontology()


def ids(store, *mentions):
    return resolve(store.graph, ONT, [Mention(n, t) for n, t in mentions])


def entity_count(store):
    return store.graph.stats()["entities"]


def test_same_normalised_name_is_one_entity_across_documents(store):
    first = ids(store, ("Asha Rao", "Person"))
    second = ids(store, ("Dr. Asha  RAO", "Person"), ("Asha Rao", "Person"))
    assert set(first.ids.values()) == set(second.ids.values()) and entity_count(store) == 1
    assert first.created == 1 and second.created == 0 and second.merged >= 1


def test_identity_is_deterministic_and_class_scoped(store):
    key = name_key("Atlas")
    r = ids(store, ("Atlas", "Product"), ("Atlas", "Person"))
    assert r.ids[("thing", key)] == entity_id("thing", key)
    assert r.ids[("thing", key)] != r.ids[("person", key)] and entity_count(store) == 2
    assert ids(store, ("Atlas", "Project")).ids[("thing", key)] == entity_id(
        "thing", key
    )  # same class


def test_short_person_name_joins_the_unique_longer_name_in_the_document(store):
    r = ids(store, ("Asha", "Person"), ("Asha Rao", "Person"), ("Leela Das", "Person"))
    assert r.ids[("person", "asha")] == r.ids[("person", "asha rao")]
    assert r.ids[("person", "asha")] != r.ids[("person", "leela das")] and entity_count(store) == 2
    # the short form is remembered, so a later document that only says "Asha" links to her
    later = ids(store, ("Asha", "Person"))
    assert (
        later.ids[("person", "asha")] == r.ids[("person", "asha rao")] and entity_count(store) == 2
    )


def test_ambiguous_short_name_is_not_merged(store):
    r = ids(store, ("Asha", "Person"), ("Asha Rao", "Person"), ("Asha Mehta", "Person"))
    assert len(set(r.ids.values())) == 3


def test_organisation_short_name_must_be_a_prefix_and_products_never_merge(store):
    r = ids(
        store,
        ("Niruthi", "Organization"),
        ("Niruthi Climate and Ecosystems Pvt. Ltd.", "Organization"),
        ("Climate", "Organization"),
        ("Beacon", "Product"),
        ("Beacon firmware", "Product"),
    )
    assert (
        r.ids[("organization", "niruthi")]
        == r.ids[("organization", "niruthi climate and ecosystem")]
    )
    assert r.ids[("organization", "climate")] != r.ids[("organization", "niruthi")]
    assert r.ids[("thing", "beacon")] != r.ids[("thing", "beacon firmware")]


def test_existing_entity_wins_when_a_new_longer_name_contains_a_known_short_one(store):
    old = ids(store, ("Niruthi", "Organization"))
    new = ids(
        store, ("Niruthi", "Organization"), ("Niruthi Climate & Ecosystem Services", "Organization")
    )
    assert set(new.ids.values()) == set(old.ids.values()) and entity_count(store) == 1
    assert store.graph.entity(next(iter(old.ids.values())))["aliases"]


def test_fuzzy_match_requires_high_similarity_and_identical_numbers(store):
    base = ids(
        store,
        ("Northfield Capital Partners International", "Organization"),
        ("Quarterly Planning Review 2024", "Project"),
    )
    typo = ids(
        store,
        ("Northfield Capital Partners Internatonal", "Organization"),  # a real typo: a new key
        ("Quarterly Planning Review 2025", "Project"),  # nearly identical text, other number
    )
    assert (
        typo.ids[("organization", "northfield capital partner internatonal")]
        == base.ids[("organization", "northfield capital partner international")]
    )
    assert (
        typo.ids[("thing", "quarterly planning review 2025")]
        != base.ids[("thing", "quarterly planning review 2024")]
    )


def test_scan_mentions_counts_every_spelling_in_every_chunk():
    surfaces = {"e1": {"Asha Rao", "Asha"}, "e2": {"Monsoon Lens"}}
    chunks = [
        ("c1", "**Asha Rao** leads Monsoon Lens. Asha agrees."),
        ("c2", "Nothing here."),
        ("c3", "Monsoon  Lens ships weekly; Ashamed is not Asha."),
    ]
    rows = set(scan_mentions(chunks, surfaces))
    assert rows == {("e1", "c1", 2), ("e2", "c1", 1), ("e2", "c3", 1), ("e1", "c3", 1)}
    assert scan_mentions(chunks, {}) == []


def test_acronym_and_its_expansion_are_one_entity_but_ambiguous_ones_are_not(store):
    expansion = "Terrestrial Observation and Prediction System"
    r = ids(store, ("TOPS", "Product"), ("NASA TOPS", "Product"), (expansion, "Product"))
    assert r.ids[("thing", name_key("TOPS"))] == r.ids[("thing", name_key(expansion))]
    # a name that merely contains the acronym is something else: missed merges beat wrong ones
    assert r.ids[("thing", name_key("NASA TOPS"))] != r.ids[("thing", name_key(expansion))]
    other = ids(
        store,
        ("API", "Product"),
        ("Application Programming Interface", "Product"),
        ("Advanced Parts Inventory", "Product"),
    )
    assert (
        other.ids[("thing", name_key("API"))]
        != other.ids[("thing", name_key("Application Programming Interface"))]
    )
    assert entity_count(store) == 5


def test_a_name_containing_an_acronym_is_not_merged_into_its_expansion(store):
    r = ids(
        store,
        ("Amazon Web Services", "Product"),
        ("AWS", "Product"),
        ("AWS Lambda", "Product"),
        ("AWS S3", "Product"),
    )
    cloud = r.ids[("thing", name_key("Amazon Web Services"))]
    assert r.ids[("thing", name_key("AWS"))] == cloud
    assert len({r.ids[("thing", name_key(n))] for n in ("AWS Lambda", "AWS S3")} | {cloud}) == 3


def test_a_complete_legal_name_is_not_an_abbreviation_of_a_longer_organisation(store):
    r = ids(
        store,
        ("Apple Inc.", "Organization"),
        ("Apple Valley School District", "Organization"),
    )
    assert (
        r.ids[("organization", name_key("Apple Inc."))]
        != r.ids[("organization", name_key("Apple Valley School District"))]
    )
