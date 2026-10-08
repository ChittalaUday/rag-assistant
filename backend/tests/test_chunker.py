from knowledge_engine.ingestion.chunker import chunk_document
from knowledge_engine.ingestion.quality import extractable


def covers(text, chunks):
    covered = set()
    for c in chunks:
        covered.update(range(c.char_start, c.char_end))
    return all(i in covered for i, ch in enumerate(text) if not ch.isspace())


def test_chunks_are_exact_slices_covering_all_text_within_size():
    text = "".join(f"Mira reviews the postmortem number {i}.\n" for i in range(80))
    chunks = chunk_document(text)
    assert all(text[c.char_start : c.char_end] == c.text for c in chunks)
    assert covers(text, chunks)
    assert all(len(c.text) <= 800 for c in chunks)
    assert [c.position for c in chunks] == list(range(len(chunks)))
    assert all(c.text.startswith("Mira") for c in chunks)
    # neighbouring chunks share a whole sentence so facts spanning a boundary survive
    assert any(
        chunks[i].text.splitlines()[-1] in chunks[i + 1].text for i in range(len(chunks) - 1)
    )


def test_long_unbroken_line_splits_at_word_boundaries():
    text = "word " * 400
    chunks = chunk_document(text)
    assert len(chunks) > 1 and covers(text, chunks)
    assert all(c.text.endswith(" ") or c.char_end == len(text) for c in chunks)


def test_headings_form_breadcrumbs_and_sections_stay_together():
    body = "Sentence about the topic. " * 20
    text = f"# Policy\n\nIntro.\n\n## Leave\n\n{body}\n\n## Travel\n\n{body}\n"
    chunks = chunk_document(text)
    assert covers(text, chunks)
    by_heading = {c.heading for c in chunks}
    assert {"Policy › Leave", "Policy › Travel"} <= by_heading
    travel = next(c for c in chunks if c.heading == "Policy › Travel")
    assert travel.text.lstrip().startswith("## Travel")


def test_chunk_never_ends_on_a_dangling_heading():
    text = "Intro sentence. " * 40 + "\n## Next section\nBody of the next section.\n"
    for c in chunk_document(text):
        last = c.text.rstrip().splitlines()[-1]
        assert not last.startswith("## ") or len(c.text.rstrip().splitlines()) == 1


def test_stacked_headings_label_the_chunk_with_the_last_one():
    text = "## Repeated page header\n\n## 1. Scope\n\nAll full-time employees are covered by this policy.\n"
    assert chunk_document(text)[0].heading == "1. Scope"


def test_split_table_chunks_carry_the_header_row():
    rows = "".join(f"| item {i} | value {i} |\n" for i in range(60))
    text = "Intro.\n\n| Name | Amount |\n| --- | --- |\n" + rows
    chunks = chunk_document(text)
    assert len(chunks) > 1 and covers(text, chunks)
    assert chunks[0].table_header is None or chunks[0].table_header == "| Name | Amount |"
    assert chunks[-1].table_header == "| Name | Amount |"


def test_tiny_tail_is_folded_and_empty_text_yields_nothing():
    text = "A reasonably long sentence about nothing in particular. " * 13 + "Tail."
    chunks = chunk_document(text)
    assert covers(text, chunks) and len(chunks[-1].text) > 40
    assert chunk_document("") == [] and chunk_document("  \n \n") == []


def test_chunking_is_deterministic():
    text = "Sentence one is here. " * 100
    assert chunk_document(text) == chunk_document(text)


def test_extractable_skips_form_templates_and_near_duplicates():
    prose = (
        "Vikram Sen is the engineering manager and approves every leave request that spans "
        "two or more consecutive working days across both teams."
    )
    template_row = "| KPI # 1 | Self-Rating (1-5) | Manager's Rating (1-5) |\n" * 6
    chunks = chunk_document(prose + "\n\n" + template_row + "\n\n" + template_row)
    flags = extractable(chunks)
    assert flags[0] is True
    assert not any(flags[1:]) or len(flags) == 1
    again = chunk_document(prose)[0]
    assert extractable([again, again]) == [True, False]
