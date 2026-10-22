"""Keeps an answer to what the passages state.

The model sometimes adds a "fact" no passage contains (a relationship, a gender, a name pieced
together from a file name). After the answer is written, every sentence is checked against the
passages and a sentence they do not support is removed from the answer.

The check is claim-level verification as in the RAG faithfulness literature: one sentence (claim)
against the evidence, supported or not. Sentences made only of words the passages use are accepted;
the others are judged by the answer model in small batches and each flagged one is confirmed on its
own.
"""

import re
from typing import Any

from knowledge_engine.context import AppContext
from knowledge_engine.reasoning.evidence import Source
from knowledge_engine.text import STOPWORDS, script_of, token_forms

INSTRUCTION = """You are a strict fact checker. List the numbers of the STATEMENTS that add a fact the SOURCE passages do not contain (an empty list when none does).
- A statement is unsupported only when it states a specific fact (a name, number, date, role, gender, relationship or event) that no passage states. Never use outside knowledge: a plausible fact the passages do not state is unsupported (for example "wife", "brother", or "she" for a person whose gender is not stated).
- Under each statement are the words the passages lack. Decide whether they carry a new fact or are only other words for what the passages say.
- A statement that rephrases, summarises or introduces what the passages say is supported, and so is a label for it. A heading or name followed by a title states that the person has it ("#### Dr. A. Rao" then "Founder" states that Dr. A. Rao is the Founder).
- A file name or image name (for example "Dr_Rama.png") is not a person's name.
- Similar names in different passages do not make the people related or different people: a relationship must be written in a passage.
Return JSON only."""


CITE = re.compile(r"[\[(]\s*(S\d+(?:\s*,\s*S\d+)*)\s*[\])]")  # [S1, S2] or (S1)


def claim_text(statement: str) -> str:
    """The sentence as a claim: without citation tags and list or bold markup."""
    text = CITE.sub("", statement)
    return re.sub(r"\s+", " ", re.sub(r"^\s*[-*+]\s+|\*\*|__", "", text)).strip()


def cited_aliases(statement: str) -> list[str]:
    """The passages the model tagged the sentence with, as it wrote them."""
    return list(
        dict.fromkeys(a for m in CITE.finditer(statement) for a in re.findall(r"S\d+", m[1]))
    )


def checkable(statement: str) -> bool:
    """Headings, bold labels, list introductions ("...:"), table rows and list-item fragments
    ("- Market information") state nothing to check on their own."""
    text = statement.strip()
    label = re.fullmatch(r"(?:[-*+]\s+)?(?:\*\*|__)[^*_]+(?:\*\*|__):?", text)
    if text.startswith(("#", "|")) or label or text.endswith(":"):  # "|": a table row
        return False
    item = re.match(r"[-*+]\s", text) is not None
    return len(re.findall(r"\w+", claim_text(text))) >= (5 if item else 3)


def states_something(statement: str) -> bool:
    """Whether a sentence of the answer is more than a heading."""
    return not statement.lstrip().startswith("#") and bool(re.search(r"\w", statement))


def schema(count: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "unsupported": {
                "type": "array",
                "maxItems": count,
                "items": {"type": "integer", "minimum": 1, "maximum": count},
            }
        },
        "required": ["unsupported"],
    }


def render(statements: list[str], sources: list[Source]) -> str:
    known = {t for s in sources for t in token_forms(s.text)}
    passages = "\n\n".join(f"{s.alias}:\n{s.text}" for s in sources)
    lines = []
    for n, text in enumerate(statements, 1):
        words = [t for t in lacking(text, known) if t not in STOPWORDS]
        lines.append(f"{n}. {text}\n   (words the passages lack: {', '.join(words) or 'none'})")
    return f"{INSTRUCTION}\n\nSOURCE:\n{passages}\n\nSTATEMENTS:\n" + "\n".join(lines)


BATCH = 10  # statements judged per model call: long lists make the model lose count


# words an answer uses to rephrase or introduce a fact, not to add one: never "new facts"
FILLER = frozenset(
    [
        "described",
        "describes",
        "describe",
        "listed",
        "lists",
        "identified",
        "referred",
        "called",
        "known",
        "stated",
        "states",
        "mentioned",
        "noted",
        "includes",
        "include",
        "including",
        "included",
        "provides",
        "provide",
        "provided",
        "offers",
        "offer",
        "offered",
        "outlines",
        "outlined",
        "covers",
        "covered",
        "based",
        "various",
        "several",
        "overall",
        "additionally",
        "however",
        "therefore",
        "specific",
        "specifically",
        "general",
        "mainly",
        "main",
        "key",
        "core",
        "also",
    ]
)


def lacking(statement: str, known: set[str]) -> list[str]:
    """The words of a statement that the passages do not use (a name, number, role, relationship)."""
    return sorted(
        {t for t in token_forms(statement) if t not in known and len(t) > 2 and t not in FILLER}
    )


def novel(statement: str, known: set[str]) -> bool:
    """Whether the statement uses a word (a name, number, role, relationship) that neither the
    passages nor the question contain. A sentence made only of words the passages use is left
    alone: that is what lets the judge look at the few suspect sentences instead of all of them.
    ponytail: word overlap cannot catch a wrong combination of known words; a claim-level
    entailment model over every sentence would (none with a commercial licence was accurate enough
    on name-and-title layouts in testing)."""
    return any(t not in STOPWORDS for t in lacking(statement, known))


async def supported(
    ctx: AppContext, statements: list[str], sources: list[Source], question: str = ""
) -> list[bool]:
    """Whether the passages support each statement; one that cannot be judged counts as supported.

    Only sentences with a word the passages lack go to the answer model, in small batches."""
    verdicts = [True] * len(statements)
    text = "\n".join(s.text for s in sources)
    known = set(token_forms(text)) | set(token_forms(question))
    todo = [i for i, t in enumerate(statements) if checkable(t)]
    script = script_of(text)
    # an answer in another script than the passages shares no words with them: cannot be judged
    todo = [i for i in todo if script_of(statements[i]) == script]
    todo = [i for i in todo if novel(statements[i], known)]
    if not todo or not sources:
        return verdicts
    for start in range(0, len(todo), BATCH):
        batch = todo[start : start + BATCH]
        asked = [statements[i] for i in batch]
        raw = await ctx.model.generate(render(asked, sources), schema(len(asked)))
        flagged = [
            batch[n - 1]
            for n in raw.get("unsupported", [])
            if isinstance(n, int) and 1 <= n <= len(asked)
        ]
        for i in flagged:  # a batch verdict is noisy: confirm each flagged sentence on its own
            alone = await ctx.model.generate(
                render([statements[i]], sources), schema(1), think=ctx.settings.verify_thinking
            )
            verdicts[i] = 1 not in alone.get("unsupported", [])
    return verdicts


def remove_statements(text: str, statements: list[str]) -> str:
    """The answer without the given sentences; a list item or line left empty goes with them."""
    for statement in statements:
        text = text.replace(statement, "", 1)
    lines = [line.rstrip() for line in text.split("\n")]
    lines = [line for line in lines if not re.fullmatch(r"\s*(?:[-*+]|\d+\.)", line)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
