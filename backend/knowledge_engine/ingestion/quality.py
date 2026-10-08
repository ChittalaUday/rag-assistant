"""Chunk quality gate: decide which chunks are worth an LLM extraction call.

Empty form templates, boilerplate tables and near-duplicate rows carry no extractable facts but
still cost a model call each and add noise entities. They remain searchable as text.
"""

import re

from knowledge_engine.ingestion.chunker import Chunk

MIN_WORDS = 3  # anything shorter holds no statement
REPETITION_FROM_WORDS = 12  # below this, repeated words are normal ("Mira leads Mira's team")
MIN_DISTINCT_RATIO = 0.3
NEAR_DUPLICATE = 0.8


def _words(text: str) -> list[str]:
    return [w.casefold() for w in re.findall(r"[^\W_]{2,}", text)]  # letters and digits


def extractable(chunks: list[Chunk]) -> list[bool]:
    """Per chunk: has prose, is not mostly repetition, is not a near copy of an earlier chunk."""
    seen: list[set[str]] = []
    flags: list[bool] = []
    for chunk in chunks:
        words = _words(chunk.text)
        distinct = set(words)
        ok = len(words) >= MIN_WORDS and (
            len(words) < REPETITION_FROM_WORDS or len(distinct) / len(words) >= MIN_DISTINCT_RATIO
        )
        if ok and any(
            len(distinct & other) / len(distinct | other) >= NEAR_DUPLICATE for other in seen
        ):
            ok = False
        seen.append(distinct)
        flags.append(ok)
    return flags
