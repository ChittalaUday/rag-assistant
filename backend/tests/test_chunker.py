from knowledge_engine.ingestion.chunker import chunk_document


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


def test_a_tiny_tail_is_never_folded_beyond_the_size_limit():
    text = "Alpha beta gamma delta epsilon. " * 21 + "Tail sentence here. " * 4
    chunks = chunk_document(text)
    assert covers(text, chunks) and all(len(c.text) <= 700 for c in chunks)


def test_a_short_final_section_keeps_its_own_heading():
    body = "Sentence about the leave policy and its many rules. " * 25
    text = f"# Policy\n\n## Leave\n\n{body}\n## End\n\nOk.\n"
    chunks = chunk_document(text)
    assert covers(text, chunks)
    last = chunks[-1]
    assert last.heading == "Policy › End" and last.text.startswith("## End")
    assert all("## End" not in c.text for c in chunks[:-1])


def test_indian_language_sentences_end_at_the_danda_and_urdu_full_stop():
    for sentence in ("समिति के 340 सदस्य हैं। ", "یہاں 18,000 کتابیں موجود ہیں۔ "):
        text = sentence * 60
        chunks = chunk_document(text)
        assert len(chunks) > 1 and covers(text, chunks)
        for c in chunks:  # every chunk starts and ends on a sentence boundary
            assert c.text.startswith(sentence.strip()[:4])
            assert c.text.rstrip().endswith(sentence.strip()[-1])


def test_repeated_text_keeps_exact_offsets():
    paragraph = "The same paragraph appears again and again in this document.\n\n"
    text = "# Repeats\n\n" + paragraph * 40
    for c in chunk_document(text):
        assert text[c.char_start : c.char_end] == c.text


def test_a_long_paragraph_without_punctuation_respects_the_limit():
    text = " ".join(f"token{i}" for i in range(2000))
    chunks = chunk_document(text, max_chars=500)
    assert covers(text, chunks) and all(len(c.text) <= 500 for c in chunks)
    assert all(not c.text.strip().startswith("oken") for c in chunks)  # never inside a word


def test_random_documents_are_covered_by_exact_bounded_slices_under_their_headings():
    import random

    from knowledge_engine.ingestion.chunker import _headings

    rng = random.Random(7)
    for _ in range(40):
        parts = []
        for i in range(rng.randint(5, 60)):
            if rng.random() < 0.15:
                parts.append(f"{'#' * rng.randint(1, 3)} Section {i}\n\n")
            words = " ".join(
                rng.choice(["alpha", "beta", "गामा", "தமிழ்", "x"])
                for _ in range(rng.randint(1, 60))
            )
            parts.append(words + rng.choice([". ", "।\n", "\n\n", "? "]))
        text = "".join(parts)
        chunks = chunk_document(text)
        assert covers(text, chunks)
        starts, paths, _ = _headings(text)
        for c in chunks:
            assert text[c.char_start : c.char_end] == c.text and len(c.text) <= 700
            # a chunk's heading is the breadcrumb of a heading at or before the chunk's text
            assert c.heading is None or c.heading in paths


def test_hybrid_scores_move_the_cut_to_the_topic_change_and_off_references():
    from knowledge_engine.ingestion.chunker import refers_back, sentence_spans

    leave = "Leave requests need written approval from the manager. " * 4
    pay = "Salaries are paid on the last working day of the month. " * 4
    more = "Payroll questions go to the finance desk. " * 6
    text = leave + pay + more
    ends = [e for _, e in sentence_spans(text)]
    topic_change = len(leave)
    scores = dict.fromkeys(ends, 0.1)
    scores[topic_change] = 0.9  # leave -> pay
    plain = chunk_document(text, max_chars=500, min_chars=150)
    hybrid = chunk_document(text, max_chars=500, min_chars=150, break_scores=scores)
    assert plain[0].char_end != topic_change and hybrid[0].char_end == topic_change
    assert covers(text, hybrid) and all(len(c.text) <= 500 for c in hybrid)
    # a sentence that refers back is never cut from the one before it
    assert refers_back("This policy applies to everyone.") and refers_back("वह 2011 में बनी।")
    assert not refers_back("The policy applies to everyone.")
    scores = dict.fromkeys(ends, 0.5)
    scores[topic_change] = -0.5  # the next sentence refers back
    kept = chunk_document(text, max_chars=500, min_chars=150, break_scores=scores)
    assert all(c.char_end != topic_change for c in kept)
