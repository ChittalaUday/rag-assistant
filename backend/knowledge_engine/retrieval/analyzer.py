"""Query understanding: resolve follow-ups, link entities, classify intent, set retrieval depth."""

import re
from collections.abc import Awaitable, Callable
from typing import Any

from knowledge_engine.context import AppContext
from knowledge_engine.extraction.ontology import Ontology
from knowledge_engine.retrieval.models import LinkedEntity, QueryPlan
from knowledge_engine.store import Store
from knowledge_engine.store.chunks import ChunkRepo, edit_distance
from knowledge_engine.store.graph import GraphRepo
from knowledge_engine.text import STOPWORDS, ordered_terms, token_forms

Notify = Callable[[str, str], Awaitable[None]]

_REFERENCE = re.compile(
    r"\b(he|she|they|them|his|her|hers|their|it|its|this|that|these|those|there|then|also|too|"
    r"same|former|latter|above|previous)\b|^(and|but|so|also|what about|how about)\b",
    re.IGNORECASE,
)
_OVERVIEW = re.compile(
    r"\b(list|all|every|each|overview|summar\w+|compare|comparison|differences?|different|"
    r"various|main|types? of|kinds? of)\b|^\s*(which|what) (\w+ )?(are|were)\b",
    re.IGNORECASE,
)
_RELATION_CUE = re.compile(
    r"\b(report\w*|manag\w+|lead\w*|led|head\w*|own\w*|responsible|depend\w*|"
    r"work\w* (for|with|under)|"
    r"related|relationship|connected|between|collaborat\w+|approv\w+|founded|supplier|provid\w+)\b",
    re.IGNORECASE,
)
MAX_NGRAM = 5
FUZZY_MIN_SIMILARITY = 0.7


def correct_spelling(graph_store: Store, text: str) -> tuple[str, dict[str, str]]:
    """Fix words that occur nowhere in the corpus but are a typo away from a word that does.

    Capitalised words (names) and words with digits or apostrophes are left alone; entity linking
    handles misspelt names separately.
    """
    pieces = list(re.finditer(r"\S+", text))
    cores: list[tuple[int, int, str, bool]] = []  # (start, end, word, has inner punctuation)
    for m in pieces:
        lead, core, _ = re.fullmatch(r"(\W*)(.*?)(\W*)", m.group(), re.S).groups()  # type: ignore[union-attr]
        bare = re.sub(r"[\W_]+", "", core)
        if not bare or any(c.isdigit() for c in bare) or "'" in core or "’" in core:
            continue
        if core[:1].isupper() or bare.casefold() in STOPWORDS:
            continue  # names (even first in the sentence) and function words are never "typos"
        start = m.start() + len(lead)
        cores.append((start, start + len(core), bare.casefold(), bare != core))
    words = [word for _, _, word, _ in cores]
    fixes, known = graph_store.chunks.spelling(words), graph_store.chunks.known_words(words)
    out, last, applied = [], 0, {}
    for start, end, word, dirty in cores:
        replacement = fixes.get(word) or (word if dirty and word in known else None)
        if replacement:  # a typo, or a real word with a stray character inside ("holi;days")
            out += [text[last:start], replacement]
            applied[text[start:end]] = replacement
            last = end
    out.append(text[last:])
    return ("".join(out), applied) if applied else (text, {})


def needs_context(question: str) -> bool:
    """Short or reference-heavy questions depend on the conversation."""
    return len(question.split()) <= 6 or bool(_REFERENCE.search(question))


def classify(question: str, entities: list[LinkedEntity]) -> str:
    if _OVERVIEW.search(question) and len({e.name.casefold() for e in entities}) < 2:
        return "overview"
    distinct = {e.name.casefold() for e in entities}
    if len(distinct) >= 2 or (entities and _RELATION_CUE.search(question)):
        return "relationship"
    return "lookup"


def _word_links(
    graph: GraphRepo, chunks: ChunkRepo, ontology: Ontology, words: list[str]
) -> list[LinkedEntity]:
    """Names given by one word: a first or last name ("Leela" for Leela Das) or a misspelt one-word
    name ("niruti"). The word must be, or be one typo from, a whole word of exactly one named
    entity, and be used in the corpus almost only as part of that name (so "policy" never links to
    "Remote Policy")."""
    keywords = {k for p in ontology.predicates for k in ontology.keywords(p)}
    words = [
        w for w in dict.fromkeys(words) if len(w) >= 4 and w not in STOPWORDS and w not in keywords
    ]
    if not words:
        return []
    known, df = chunks.known_words(words), chunks.doc_freq(words)
    out: list[LinkedEntity] = []
    for word in words:
        allowed = 0 if word in known else (1 if len(word) <= 8 else 2)
        if allowed and len(word) < 5:
            continue
        best: dict[str, tuple[int, Any]] = {}
        for r in graph.token_candidates(word):
            etype = ontology.types.get(r["type"])
            if etype is None or not etype.standalone or df.get(word, 0) > 2 * r["passages"] + 1:
                continue
            distance = min(edit_distance(word, token) for token in r["matched"].split())
            if distance <= allowed and distance < best.get(r["id"], (99, None))[0]:
                best[r["id"]] = (distance, r)
        if best:
            nearest = min(d for d, _ in best.values())
            winners = [r for d, r in best.values() if d == nearest]
            if len(winners) == 1:  # two entities share the word: ambiguous, link neither
                r = winners[0]
                out.append(LinkedEntity(r["id"], r["name"], r["type"], word, 0))
    return out


def link_entities(
    graph: GraphRepo, ontology: Ontology, text: str, chunks: ChunkRepo | None = None
) -> list[LinkedEntity]:
    """Match question n-grams against entity names and aliases (longest match wins). With `chunks`,
    one-word names are also matched loosely (first names, single-word misspellings)."""
    tokens = token_forms(text)
    grams = {
        " ".join(tokens[i : i + n]): n
        for n in range(1, MAX_NGRAM + 1)
        for i in range(len(tokens) - n + 1)
    }
    rows = graph.link(list(grams))
    found: dict[str, LinkedEntity] = {}
    for r in rows:
        key, etype = r["matched"], ontology.types.get(r["type"])
        # a lone common word must not link: only distinctive single tokens of named types
        if " " not in key and (
            len(key) < 4 or key in STOPWORDS or etype is None or not etype.standalone
        ):
            continue
        found.setdefault(r["id"], LinkedEntity(r["id"], r["name"], r["type"], key, r["degree"]))
    # typo tolerance for multi-word names ("arun balla" vs "Arun Ballah")
    linked_text = " ".join(e.matched for e in found.values())
    fuzzy = [
        (gram, n)
        for gram, n in grams.items()
        if n >= 2 and gram not in linked_text and not any(t in STOPWORDS for t in gram.split())
    ]
    for gram, _ in sorted(fuzzy, key=lambda g: g[1])[:12]:  # filter first, then cap, short first
        for r in graph.fuzzy_link(gram, FUZZY_MIN_SIMILARITY, 1):
            found.setdefault(r["id"], LinkedEntity(r["id"], r["name"], r["type"], gram, 0))
    if chunks is not None:
        covered = {t for e in found.values() for t in e.matched.split()}
        for e in _word_links(graph, chunks, ontology, [t for t in tokens if t not in covered]):
            found.setdefault(e.id, e)
    # drop entities matched only by a fragment of a longer matched name
    keys = [e.matched for e in found.values()]
    return [
        e
        for e in found.values()
        if not any(e.matched != k and f" {e.matched} " in f" {k} " for k in keys)
    ]


async def rewrite(ctx: AppContext, question: str, history: list[dict[str, Any]]) -> str | None:
    """Make a follow-up self-contained with one small model call; None if it cannot be trusted."""
    turns = "\n".join(f"{m['role']}: {str(m['content'])[:300]}" for m in history[-4:])
    prompt = (
        "Rewrite the FINAL QUESTION so it can be understood without the conversation: replace "
        "pronouns and vague references with the names they refer to. Keep it short and do not "
        "answer it. If it is already self-contained, return it unchanged. Return JSON only.\n"
        f"CONVERSATION (oldest first):\n{turns}\nFINAL QUESTION: {question}"
    )
    schema = {
        "type": "object",
        "properties": {"question": {"type": "string", "maxLength": 400}},
        "required": ["question"],
    }
    try:
        result = str((await ctx.model.generate(prompt, schema)).get("question", "")).strip()
    except ValueError:
        return None
    return result if 0 < len(result) <= 400 else None


async def analyze(
    ctx: AppContext, question: str, history: list[dict[str, Any]], notify: Notify
) -> QueryPlan:
    search_text, rewritten = question, False
    if history and needs_context(question):
        previous = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        resolved = None
        if ctx.settings.rewrite_followups:
            await notify("rewriting", "Resolving references to the earlier conversation")
            resolved = await rewrite(ctx, question, history)
        if resolved:  # unchanged means the model judged it self-contained: trust that
            search_text, rewritten = resolved, resolved != question
        elif previous:
            search_text = f"{previous}\n{question}"  # lexical fallback keeps earlier names in play
    search_text, corrections = correct_spelling(ctx.store, search_text)
    entities = link_entities(ctx.store.graph, ctx.ontology, search_text, ctx.store.chunks)
    intent = classify(question, entities)
    mode = ctx.settings.graph_retrieval
    return QueryPlan(
        question=question,
        search_text=search_text,
        rewritten=rewritten,
        terms=ordered_terms(search_text),
        entities=entities,
        intent=intent,
        final_k={"lookup": 8, "relationship": 10, "overview": 14}[intent],
        hops=2 if intent == "relationship" else 1,
        corrections=corrections,
        seeds=entities
        if mode == "always" or (mode == "relationship" and intent == "relationship")
        else [],
    )
