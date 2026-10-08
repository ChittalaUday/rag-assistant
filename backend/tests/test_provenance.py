from knowledge_engine.provenance import source_urls


def test_explicit_source_headers_only():
    assert source_urls(
        [
            "Source: https://niruthi.com/about\nSource: https://niruthi.com/team\n"
            "Read https://other.example/path in the body.\nSource: javascript:alert(1)\n",
            "Source: https://niruthi.com/about\nSource: http://insecure.example\n"
            "Source: https://user:password@example.com\nSource: https://example.com:bad\n",
        ]
    ) == ["https://niruthi.com/about", "https://niruthi.com/team"]
