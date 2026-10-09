"""Per-statement attribution: which of the given passages state each sentence of an answer.

The answer model cites passages for the answer as a whole and tends to cite only the first passage
that supports a fact, not the others that corroborate it. A second, narrower call maps every
sentence to the passages that state it, which completes the citations and marks sentences that no
passage supports.
"""

import re
from typing import Any

from knowledge_engine.context import AppContext
from knowledge_engine.reasoning.evidence import Source
from knowledge_engine.text import STOPWORDS, script_of, token_forms

INSTRUCTION = """For each numbered STATEMENT, list every SOURCE passage that supports it.
- A passage supports a statement when it states at least one of the facts the statement contains, with the same meaning (the wording and the language may differ). A statement that combines facts from several passages lists all of them.
- A passage that only mentions the same name or topic without stating any of its facts does not count.
- A statement with no factual claim of its own (for example "The passages disagree." or an introduction to a list) gets an empty list.
- Judge only from the passage text; never use outside knowledge.
Return JSON only."""

# a sentence ends at . ! ? or the danda (। ॥) before a letter, never after an abbreviation or an
# initial; list items are separated by line breaks
_END = re.compile(r"(?<=[.!?।॥])\s+(?=[^\W\d_])")
ABBREVIATIONS = frozenset(
    [
        "dr",
        "mr",
        "mrs",
        "ms",
        "prof",
        "sr",
        "jr",
        "st",
        "no",
        "vs",
        "etc",
        "ltd",
        "pvt",
        "inc",
        "co",
        "corp",
        "llc",
        "plc",
        "gmbh",
        "dept",
        "approx",
        "fig",
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",
    ]
)


def _ends_sentence(before: str, after: str) -> bool:
    words = before.rstrip(".!?").split()
    word = words[-1].casefold() if words else ""
    if before.endswith(".") and (word in ABBREVIATIONS or len(word) == 1):
        return False  # "Dr. Rama", "Pvt. Ltd.", "J. Smith"
    return not after[:1].islower()  # "Inc. and its partners" continues the sentence


def split_statements(text: str) -> list[str]:
    statements: list[str] = []
    for line in text.splitlines():
        start = 0
        for m in _END.finditer(line):
            if _ends_sentence(line[start : m.start()], line[m.end() :]):
                statements.append(line[start : m.start()])
                start = m.end()
        statements.append(line[start:])
    return [s.strip() for s in statements if re.search(r"\w", s)]


def shares_content(statement: str, passage: str) -> bool:
    """A cheap guard on the model's matches: the passage must contain at least two of the
    statement's content words (one, if the statement has only one). It drops matches such as a
    boilerplate passage that states none of the statement's facts. A statement in another script
    than the passage (an answer in the question's language) cannot be checked this way."""
    if script_of(statement) != script_of(passage):
        return True  # an answer in another language than the passage shares no words to check
    terms = {t for t in token_forms(statement) if len(t) > 2 and t not in STOPWORDS}
    found = terms & set(token_forms(passage))
    return len(found) >= min(2, len(terms))


def schema(aliases: list[str], count: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "statements": {
                "type": "array",
                "maxItems": count,
                "items": {
                    "type": "object",
                    "properties": {
                        "n": {"type": "integer", "minimum": 1, "maximum": count},
                        "sources": {
                            "type": "array",
                            "maxItems": len(aliases),
                            "items": {"type": "string", "enum": aliases},
                        },
                    },
                    "required": ["n", "sources"],
                },
            }
        },
        "required": ["statements"],
    }


def render(statements: list[str], sources: list[Source]) -> str:
    passages = "\n\n".join(f"{s.alias}:\n{s.text}" for s in sources)
    numbered = "\n".join(f"{n}. {text}" for n, text in enumerate(statements, 1))
    return f"{INSTRUCTION}\n\nSOURCE:\n{passages}\n\nSTATEMENTS:\n{numbered}"


async def attribute(
    ctx: AppContext, statements: list[str], sources: list[Source]
) -> list[list[str]]:
    """Aliases of the passages stating each statement (empty list: none does)."""
    if not statements or not sources:
        return [[] for _ in statements]
    aliases = [s.alias for s in sources]
    raw = await ctx.model.generate(render(statements, sources), schema(aliases, len(statements)))
    text = {s.alias: s.text for s in sources}
    found: list[list[str]] = [[] for _ in statements]
    for item in raw.get("statements", []):
        n = item.get("n")
        if isinstance(n, int) and 1 <= n <= len(statements):
            found[n - 1] = list(
                dict.fromkeys(
                    a
                    for a in item.get("sources", [])
                    if a in text and shares_content(statements[n - 1], text[a])
                )
            )
    return found
