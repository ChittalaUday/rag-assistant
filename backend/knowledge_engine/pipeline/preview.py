"""Live preview of a question as it is typed: the passages it is likely to draw on, and the names
and relations those passages state about what is being typed.

No model is called (neither embedding nor generation): misspellings are fixed from the corpus
vocabulary the way a sent question is fixed (leaving the word still being typed), then a keyword
search finds the best passages, one per document section.

The graph is read off the passage text, not extracted by a model: names are runs of capitalised
words, and two names in one sentence that shares a typed term are related by the words between
them. Every relation quotes the sentence it comes from.
"""

import re
from typing import Any

from knowledge_engine.context import AppContext
from knowledge_engine.retrieval.analyzer import correct_spelling
from knowledge_engine.retrieval.hop import _SENTENCE
from knowledge_engine.text import STOPWORDS, WORD, ordered_terms, script_of, words

MAX_PASSAGES = 5
MAX_RELATIONS = 15
MAX_GRAPH_RELATIONS = 400
MAX_LABEL_WORDS = 6


def preview(ctx: AppContext, text: str, exclude_labels: list[str] | None = None) -> dict[str, Any]:
    head, space, tail = text.rpartition(" ") if not text[-1:].isspace() else (text, "", "")
    fixed, corrections = correct_spelling(ctx.store, head)
    terms = ordered_terms(fixed + space + tail)
    found = ctx.store.chunks.sparse(terms, 4 * MAX_PASSAGES, exclude_labels or [])
    rows = ctx.store.chunks.get_many([chunk_id for chunk_id, _ in found])
    passages, seen = [], set()
    for row in rows:
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
    return {
        "passages": passages,
        "corrections": corrections,
        "graph": graph(fixed + space + tail, rows[: 2 * MAX_PASSAGES]),
    }


# what may sit between two capitalised words of one name: "Ministry of Agriculture & Welfare"
_JOIN = re.compile(r"\s+(?:(?:of|of the|for|de|da|van|von)\s+)?|\s*[&-]\s*", re.IGNORECASE)


def names(sentence: str) -> list[tuple[str, int, int, bool]]:
    """(name, start, end, opens the sentence) for each run of capitalised words.

    ponytail: cased scripts only; scripts without capitals (Indic, Arabic) yield no names.
    """
    spans: list[list[int]] = []
    for match in WORD.finditer(sentence):
        if not match.group()[:1].isupper():
            continue
        if spans and _JOIN.fullmatch(sentence[spans[-1][1] : match.start()]):
            spans[-1][1] = match.end()
        else:
            spans.append([match.start(), match.end()])
    out = []
    for start, end in spans:
        first = WORD.match(sentence, start)
        if first and first.group().casefold() in STOPWORDS:  # "The NASA" -> "NASA"
            nxt = WORD.search(sentence, first.end(), end)
            if nxt is None:
                continue
            start = nxt.start()
        out.append(
            (sentence[start:end], start, end, not sentence[:start].strip(" -*#>|\t0123456789."))
        )
    return out


def full_graph(ctx: AppContext, exclude_labels: list[str] | None = None) -> dict[str, Any]:
    """Every name and relation in the indexed passages (the graph shown before anything is typed).

    ponytail: re-read on each call; cache per index version if the corpus grows past a few
    thousand passages.
    """
    return graph("", ctx.store.chunks.all_texts(exclude_labels or []), MAX_GRAPH_RELATIONS)


def graph(text: str, rows: list[dict[str, Any]], limit: int = MAX_RELATIONS) -> dict[str, Any]:
    """Names and relations in the passages, around the terms of the typed text (all of them when
    nothing is typed)."""
    typed = {t for t in ordered_terms(text) if len(t) > 2}
    sentences = [
        (row, s)
        for row in rows
        for s in _SENTENCE.split(row["text"])
        if not typed or typed & set(ordered_terms(s))
    ]
    found = [(row, s, names(s)) for row, s in sentences]
    # a single capitalised word opening a sentence may just be capitalised; keep it only when it
    # also appears mid-sentence or as part of a longer name
    trusted = {n.casefold() for _, _, ns in found for n, _, _, first in ns if not first}
    trusted |= {w for _, _, ns in found for n, *_ in ns if " " in n for w in n.casefold().split()}
    kept = [
        (
            row,
            sentence,
            [
                (n, start, end)
                for n, start, end, first in ns
                if " " in n or not first or n.casefold() in trusted
            ],
        )
        for row, sentence, ns in found
    ]
    # "Arun" is the "Arun Ballah" of the same passages when every longer name containing it
    # extends that one ("Arun Ballah Jr"), not when two differ ("Arun Ballah", "Arun Rao")
    full = {n.casefold(): n for _, _, ks in kept for n, _, _ in ks}
    canon = {name: name for name in full}
    for name in full:
        if " " not in name:
            longer = sorted((k for k in full if " " in k and name in k.split()), key=len)
            if longer and all(k.startswith(longer[0]) for k in longer):
                canon[name] = longer[0]
    # a document's language, by the script most of its passages are written in: an English
    # table inside a Telugu document still belongs to the Telugu graph
    texts: dict[str, list[str]] = {}
    for row in rows:
        texts.setdefault(row["document_name"], []).append(row["text"])
    scripts = {name: script_of("\n".join(parts)) for name, parts in texts.items()}
    entities: dict[str, dict[str, Any]] = {}
    relations: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row, sentence, ks in kept:
        for n, _, _ in ks:
            eid = canon[n.casefold()]
            entities.setdefault(eid, {"id": eid, "name": full[eid]})
        for (a, _, a_end), (b, b_start, _) in zip(ks, ks[1:], strict=False):
            between = words(sentence[a_end:b_start])
            if between[:1] == ["s"] and sentence[a_end : a_end + 1] in "'’":  # "Acme's"
                between = between[1:]
            label = " ".join(between) if 0 < len(between) <= MAX_LABEL_WORDS else "appears with"
            key = (canon[a.casefold()], canon[b.casefold()], label.casefold())
            if key[0] == key[1] or key in relations:
                continue
            relations[key] = {
                "source": key[0],
                "target": key[1],
                "label": label,
                "chunk_id": row["id"],
                "document": row["document_name"],
                "quote": sentence.strip(),
                "script": scripts[row["document_name"]],
            }
    for entity in entities.values():
        entity["typed"] = bool(typed & set(ordered_terms(entity["name"])))
    # a typed name is found when a passage name contains it ("Arun Balla" in "CEO Arun Balla")
    unmatched = [
        n
        for n, *_ in names(text)
        if len(n) > 2 and not any(f" {n.casefold()} " in f" {k} " for k in canon)
    ]
    ranked = sorted(
        relations.values(),
        key=lambda r: (
            -(entities[r["source"]]["typed"] + entities[r["target"]]["typed"]),
            r["label"] == "appears with",
        ),
    )[:limit]
    linked = {r["source"] for r in ranked} | {r["target"] for r in ranked}
    return {
        "entities": [
            {**e, "found": True} for e in entities.values() if e["id"] in linked or e["typed"]
        ]
        + [{"id": n.casefold(), "name": n, "typed": True, "found": False} for n in unmatched],
        "relations": ranked,
    }
