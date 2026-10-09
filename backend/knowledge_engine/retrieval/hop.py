"""Second-hop retrieval: follow what the best passage names to the other documents about it.

"Who signed off on the overrun, and what is that person's title?" finds the passage naming the
person, but the title lives in a passage that shares no words with the question; only the name
links them. The best-matching sentence of the first-round passages supplies the link: its rarer
words that also occur in other passages (usually names). A full-text search on those words finds
the passages that continue the chain.

Extra passages are appended after the first-round selection and only from documents not already
selected, so a second hop can add context but never displaces what the question itself found.
Script-agnostic: words are chosen by rarity, not capitalisation.
"""

import re

from knowledge_engine.retrieval.models import Candidate, QueryPlan
from knowledge_engine.store.chunks import ChunkRepo
from knowledge_engine.text import STOPWORDS, ordered_terms

MAX_BRIDGE_TERMS = 3
MAX_EXTRA = 2
FIRST_ROUND = 3  # passages the bridge sentence may come from
_SENTENCE = re.compile(r"(?<=[.!?;।॥])\s+|\n+")


def best_sentence(question_terms: set[str], texts: list[str]) -> str:
    """The sentence sharing the most question terms (earliest wins a tie)."""
    best, best_overlap = "", 0
    for text in texts:
        for sentence in _SENTENCE.split(text):
            overlap = len(question_terms & set(ordered_terms(sentence)))
            if overlap > best_overlap:
                best, best_overlap = sentence, overlap
    return best


def names_in(sentence: str) -> set[str]:
    """Words capitalised inside the sentence (not as its first word): names, in cased scripts."""
    words = re.findall(r"\w+", sentence)
    return {w.casefold() for w in words[1:] if w[:1].isupper()}


def bridge_terms(question_terms: set[str], sentence: str, doc_freq: dict[str, int]) -> list[str]:
    """Words of the sentence the question does not use that occur in at least one other passage:
    names when the sentence has any, otherwise the rarest words (scripts without capitals)."""
    words = [
        w
        for w in ordered_terms(sentence)
        if w not in question_terms and w not in STOPWORDS and len(w) > 2 and not w.isdigit()
    ]
    linked = [w for w in words if doc_freq.get(w, 0) >= 2]
    names = names_in(sentence)
    if any(w in names for w in linked):
        linked = [w for w in linked if w in names]
    return sorted(linked, key=lambda w: doc_freq[w])[:MAX_BRIDGE_TERMS]


def second_hop(
    chunks_repo: ChunkRepo,
    plan: QueryPlan,
    selected: list[Candidate],
    texts: dict[str, str],
    documents: dict[str, str],
    exclude_labels: list[str],
) -> tuple[list[str], list[str]]:
    """(chunk ids to append, the bridge terms used). texts/documents map chunk id -> text/doc id."""
    question = set(plan.terms)
    sentence = best_sentence(question, [texts[c.chunk_id] for c in selected[:FIRST_ROUND]])
    if not sentence:
        return [], []
    candidates = [w for w in ordered_terms(sentence) if w not in question]
    terms = bridge_terms(question, sentence, chunks_repo.doc_freq(candidates))
    if not terms:
        return [], []
    seen_docs = {documents[c.chunk_id] for c in selected}
    found = chunks_repo.sparse(terms, 10, exclude_labels)
    rows = {r["id"]: r for r in chunks_repo.get_many([cid for cid, _ in found])}
    extra: list[str] = []
    for chunk_id, _ in found:
        row = rows.get(chunk_id)
        if row is None or row["document_id"] in seen_docs:
            continue
        extra.append(chunk_id)
        seen_docs.add(row["document_id"])
        if len(extra) == MAX_EXTRA:
            break
    return extra, terms
