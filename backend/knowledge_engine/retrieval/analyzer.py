"""Query understanding: resolve follow-ups, fix misspellings, classify intent, set depth."""

import re
from collections.abc import Awaitable, Callable
from typing import Any

from knowledge_engine.context import AppContext
from knowledge_engine.retrieval.models import QueryPlan
from knowledge_engine.store import Store
from knowledge_engine.text import STOPWORDS, is_latin, ordered_terms

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


def correct_spelling(store: Store, text: str) -> tuple[str, dict[str, str]]:
    """Fix words that occur nowhere in the corpus but are a typo away from a word that does.

    Capitalised words (names), words with digits or apostrophes, and words in other scripts than
    Latin are left alone: in uncased scripts a name cannot be told from a typo and an inflected form
    is one edit from its stem, so those are searched as typed; dense search still finds passages
    close in meaning.
    """
    pieces = list(re.finditer(r"\S+", text))
    cores: list[tuple[int, int, str, bool]] = []  # (start, end, word, has inner punctuation)
    for m in pieces:
        lead, core, _ = re.fullmatch(r"(\W*)(.*?)(\W*)", m.group(), re.S).groups()  # type: ignore[union-attr]
        bare = re.sub(r"[\W_]+", "", core)
        if not bare or any(c.isdigit() for c in bare) or "'" in core or "’" in core:
            continue
        if not is_latin(bare):
            continue
        if core[:1].isupper() or bare.casefold() in STOPWORDS:
            continue  # names (even first in the sentence) and function words are never "typos"
        start = m.start() + len(lead)
        cores.append((start, start + len(core), bare.casefold(), bare != core))
    words = [word for _, _, word, _ in cores]
    fixes, known = store.chunks.spelling(words), store.chunks.known_words(words)
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


def classify(question: str) -> str:
    if _OVERVIEW.search(question):
        return "overview"
    if _RELATION_CUE.search(question):
        return "relationship"
    return "lookup"


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
    intent = classify(question)
    return QueryPlan(
        question=question,
        search_text=search_text,
        rewritten=rewritten,
        terms=ordered_terms(search_text),
        intent=intent,
        final_k={"lookup": 8, "relationship": 10, "overview": 14}[intent],
        corrections=corrections,
    )
