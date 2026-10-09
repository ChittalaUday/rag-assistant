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


def test_indic_words_keep_their_vowel_signs_viramas_and_joiners():
    from knowledge_engine.text import ordered_terms, words

    cases = {
        "हिन्दी में छुट्टी नीति": ["हिन्दी", "में", "छुट्टी", "नीति"],
        "தமிழ் விடுப்பு கொள்கை": ["தமிழ்", "விடுப்பு", "கொள்கை"],
        "ছুটির নীতি": ["ছুটির", "নীতি"],
        "చెల్లింపు విధానం": ["చెల్లింపు", "విధానం"],
        "ਪੰਜਾਬੀ ਛੁੱਟੀ": ["ਪੰਜਾਬੀ", "ਛੁੱਟੀ"],
        "ಮೈಸೂರು ರೇಷ್ಮೆ": ["ಮೈಸೂರು", "ರೇಷ್ಮೆ"],
        "കൊച്ചി മത്സ്യത്തൊഴിലാളി": ["കൊച്ചി", "മത്സ്യത്തൊഴിലാളി"],
        "କଟକ ହସ୍ତତନ୍ତ": ["କଟକ", "ହସ୍ତତନ୍ତ"],
        "اردو چھٹی کی پالیسی": ["اردو", "چھٹی", "کی", "پالیسی"],
    }
    for text, expected in cases.items():
        assert words(text) == expected, text
        assert ordered_terms(text) == expected, text
    assert words("ক্ষ‍ম") == ["ক্ষ‍ম"]  # a joiner stays inside its word
    assert words("हैं। वह") == ["हैं", "वह"]  # the danda is punctuation


def test_accent_folding_applies_to_latin_letters_only():
    assert token_forms("Cafés à Pune") == ["cafe", "a", "pune"]
    precomposed, decomposed = (
        "\u0958\u093e\u0928\u0942\u0928",
        "\u0915\u093c\u093e\u0928\u0942\u0928",
    )
    assert token_forms(precomposed) == token_forms(decomposed)  # क़ानून typed two ways
    assert token_forms("हिन्दी") == ["हिन्दी"]
