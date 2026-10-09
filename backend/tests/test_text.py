from knowledge_engine.text import (
    content_terms,
    normalize_text,
    ordered_terms,
    token_forms,
)


def test_content_terms_filters_stopwords_and_single_chars():
    terms = content_terms("Who is listed as the Founder of Niruthi?")
    assert "founder" in terms and "niruthi" in terms and "listed" in terms
    assert not (terms & {"who", "is", "as", "the", "of"})


def test_ordered_terms_are_distinct_and_in_order():
    assert ordered_terms("Leave policy: the leave days, and policy") == ["leave", "policy", "days"]


def test_plain_prose_passes_through_normalisation_unchanged():
    text = "Mira leads Acme.\nAcme owns Nova.\n\nA new paragraph."
    assert normalize_text(text) == text


def test_normalisation_cleans_extraction_artifacts():
    raw = (
        "Niruthi Climate &amp; Ecosystems<!-- image -->\r\n\r\n\r\n\r\nNext line   \n"
        "| Score     | Criteria        |\n|-----------|-----------------|\n"
        "|           |                 |\n| < 60%     | Improve         |\n"
    )
    assert normalize_text(raw) == (
        "Niruthi Climate & Ecosystems\n\nNext line\n"
        "| Score | Criteria |\n| --- | --- |\n| < 60% | Improve |\n"
    )


def test_token_forms_fold_plurals_and_accents():
    assert token_forms("Ecosystems & Cafés") == ["ecosystem", "and", "cafe"]
