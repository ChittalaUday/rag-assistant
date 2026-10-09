"""Live preview of a question as it is typed: the passages it is likely to draw on.

No model is called (neither embedding nor generation): misspellings are fixed from the corpus
vocabulary the way a sent question is fixed (leaving the word still being typed), then a keyword
search finds the best passages, one per document section.
"""

from typing import Any

from knowledge_engine.context import AppContext
from knowledge_engine.retrieval.analyzer import correct_spelling
from knowledge_engine.text import ordered_terms

MAX_PASSAGES = 5


def preview(ctx: AppContext, text: str, exclude_labels: list[str] | None = None) -> dict[str, Any]:
    head, space, tail = text.rpartition(" ") if not text[-1:].isspace() else (text, "", "")
    fixed, corrections = correct_spelling(ctx.store, head)
    terms = ordered_terms(fixed + space + tail)
    found = ctx.store.chunks.sparse(terms, 4 * MAX_PASSAGES, exclude_labels or [])
    passages, seen = [], set()
    for row in ctx.store.chunks.get_many([chunk_id for chunk_id, _ in found]):
        section = (row["document_id"], row["heading"])
        if section in seen:
            continue
        seen.add(section)
        passages.append(
            {
                "chunk_id": row["id"],
                "document_id": row["document_id"],
                "document": row["document_name"],
                "heading": row["heading"],
            }
        )
        if len(passages) == MAX_PASSAGES:
            break
    return {"passages": passages, "corrections": corrections}
