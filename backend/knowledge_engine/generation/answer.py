"""Answer generation and validation: cite or abstain.

The model proposes an answer and the passages that support it; a second, narrower call maps each
sentence to the passages that state it (completing the citations), and code derives the provenance
notices. An answer without a valid citation is never published silently.
"""

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from knowledge_engine.context import AppContext
from knowledge_engine.generation.attribution import attribute, split_statements
from knowledge_engine.generation.grounding import (
    checkable,
    cited_aliases,
    remove_statements,
    states_something,
    supported,
)
from knowledge_engine.generation.prompt import build_prompt
from knowledge_engine.reasoning.evidence import Evidence, Source
from knowledge_engine.retrieval.models import QueryPlan
from knowledge_engine.text import ANSWER_LANGUAGES, script_of

Notify = Callable[[str, str], Awaitable[None]]
Draft = Callable[[str], Awaitable[None]]  # the answer text so far, before any validation
_ANSWER_KEY = re.compile(r'"answer"\s*:\s*"')
NO_EVIDENCE = "I couldn't find information about that."
# an abstention that talks about the material instead of the question (English only); replaced by
# NO_EVIDENCE. ponytail: English wording only, other languages rely on the prompt.
_META = re.compile(r"\b(?:provided|given)\b|\b(?:sources?|passages?|context|excerpts?)\b", re.I)
RETRY_NOTE = (
    "REMINDER: your previous reply cited no valid passage. Cite the SOURCE aliases (S1, S2, ...) "
    "that support each statement, or set status to insufficient_evidence if nothing supports it."
)


def language_reminder(language: str) -> str:
    """Retry note after an answer came back in the wrong language."""
    if language:
        return (
            f"REMINDER: your previous answer was not in {language}. Write the whole answer in "
            f"{language}; keep names and numbers as in the passages."
        )
    return (
        "REMINDER: your previous answer was not in the language of the QUESTION. Write the whole "
        "answer in the question's language and script; keep names and numbers as in the passages."
    )


def wrong_language(question: str, answer: str, language: str = "") -> bool:
    """The answer is not in the script of the language asked for or, with none asked for, a
    question in a non-Latin script was answered in another script (usually English)."""
    expected = ANSWER_LANGUAGES[language] if language else script_of(question)
    if not language and expected == "LATIN":
        return False
    return bool(answer) and bool(expected) and script_of(answer) != expected


_LEAD_IN = r"(?:according to|as stated in|as described in|as per|per|based on|from|in)"
_STRONG_LEAD_IN = r"(?:according to|as stated in|as described in)"
_QUOTE, _CLOSE = "[\"'\u2018\u201c]?", "[\"'\u2019\u201d]?"


def strip_document_names(text: str, names: list[str]) -> str:
    """Remove "According to <file>," style attributions and "(<file>)" asides.

    The prompt forbids document names (the sources are listed next to the answer), but a small model
    still opens with them now and then. Full file names are removed after any lead-in; a bare name
    without its extension only after the strongest lead-ins and a comma, so ordinary words that
    happen to be a file's name ("based on technology") are left alone.
    """
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        full, stem = re.escape(name), re.escape(Path(name).stem)
        article = r"(?:the\s+)?(?:document\s+|file\s+)?"
        noun = r"(?:\s+(?:document|file))?"
        text = re.sub(
            rf"(?i)\b{_LEAD_IN}\s+{article}{_QUOTE}{full}{_CLOSE}{noun}\s*[,:]?\s*", "\x00", text
        )
        if stem:
            text = re.sub(
                rf"(?i)\b{_STRONG_LEAD_IN}\s+{article}{_QUOTE}{stem}{_CLOSE}{noun}\s*[,:]\s*",
                "\x00",
                text,
            )
        text = re.sub(rf"(?i)\s*[(\[]\s*{full}\s*[)\]](?!\()", "", text)
    # a removed lead-in that opened a sentence leaves the next word to be capitalised
    text = re.sub(r"(^|(?<=[.!?]\s)|(?<=\n))\x00(\w)", lambda m: m[1] + m[2].upper(), text)
    return text.replace("\x00", "").strip()


@dataclass
class Generated:
    text: str = NO_EVIDENCE
    status: str = "insufficient_evidence"  # answered | insufficient_evidence | conflicting_evidence
    citations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # each sentence of the answer and the chunk ids of the passages that state it
    statements: list[dict[str, Any]] = field(default_factory=list)
    # why the answer model was asked again: "no_valid_citations", "wrong_language" (kept or not)
    retries: list[str] = field(default_factory=list)


def draft_answer(raw: str) -> str:
    """The "answer" string of a JSON object still being generated, as far as it has come."""
    key = _ANSWER_KEY.search(raw)
    if not key:
        return ""
    end = key.end()
    while end < len(raw) and raw[end] != '"':
        end += 2 if raw[end] == "\\" else 1
    body = re.sub(r"\\(u[0-9a-fA-F]{0,3})?$", "", raw[key.end() : end])  # escape cut mid-way
    try:
        return str(json.loads(f'"{body}"', strict=False))
    except ValueError:
        return ""


async def _ask(
    ctx: AppContext,
    plan: QueryPlan,
    history: list[dict[str, Any]],
    ev: Evidence,
    notify: Notify,
    extra: str,
    draft: Draft | None = None,
) -> dict[str, Any]:
    prompt, schema = build_prompt(plan, history, ev, extra, ctx.settings.answer_thinking)
    if ctx.model.lock.locked():
        await notify("waiting_for_model", "Waiting for the local answer model")

    async def started() -> None:
        await notify("generating", "Generating an answer from the selected source passages")

    shown = ""

    async def on_text(output: str) -> None:
        nonlocal shown
        text = draft_answer(output)
        if draft and text != shown:
            shown = text
            await draft(text)

    raw = await ctx.model.generate(
        prompt,
        schema,
        started,
        think=ctx.settings.answer_thinking,
        on_text=on_text if draft else None,
    )
    if "citation_ids" not in raw:
        raise ValueError("Model omitted required citation_ids; answer was not published")
    return raw


MAX_REVISED_STATEMENTS = 15


async def _attribute(
    ctx: AppContext, statements: list[str], sources: list[Source], known: set[str]
) -> list[list[str]]:
    """Passages for each statement. After the claim check the passages the model tagged a sentence
    with are used as they are (the sentence was just checked against all of them), and only
    checkable sentences without a tag go to the attribution call, which costs seconds."""
    if not ctx.settings.verify_claims:
        return await attribute(ctx, statements, sources)
    found = [[a for a in cited_aliases(t) if a in known] for t in statements]
    untagged = [i for i, t in enumerate(statements) if not found[i] and checkable(t)]
    if untagged:
        extra = await attribute(ctx, [statements[i] for i in untagged], sources)
        for i, aliases in zip(untagged, extra, strict=True):
            found[i] = aliases
    return found


def revise_note(unsupported: list[str]) -> str:
    listed = "\n".join(f"- {t}" for t in unsupported)
    return (
        f"REVISION: a check found that SOURCE does not state these sentences of your previous "
        f"answer:\n{listed}\nWrite the answer again with only facts SOURCE states: no name, "
        "gender or relationship it does not give. Name people instead of using pronouns. Do not "
        "mention this revision."
    )


async def _ground(
    ctx: AppContext, text: str, sources: list[Source], out: Generated, question: str
) -> tuple[str, list[str]]:
    """The answer without the sentences the passages do not support, and those sentences."""
    sentences = split_statements(text)
    try:
        holds = await supported(ctx, sentences, sources, question)
    except ValueError:  # a malformed verdict: keep the answer as written
        holds = [True] * len(sentences)
        out.warnings.append("Statements could not be checked against the passages")
    dropped = [t for t, ok in zip(sentences, holds, strict=True) if not ok]
    return (remove_statements(text, dropped) if dropped else text), dropped


async def generate_answer(
    ctx: AppContext,
    plan: QueryPlan,
    history: list[dict[str, Any]],
    ev: Evidence,
    notify: Notify,
    draft: Draft | None = None,
) -> Generated:
    """`draft` receives the first attempt's answer text as it is generated (unvalidated)."""
    if not ev.sources:
        return Generated()
    raw = await _ask(ctx, plan, history, ev, notify, "", draft)
    by_alias = {s.alias: s for s in ev.sources}

    def valid_ids(r: dict[str, Any]) -> list[str]:
        return list(dict.fromkeys(a for a in r.get("citation_ids", []) if a in by_alias))

    retries: list[str] = []
    abstained = raw.get("status") == "insufficient_evidence"
    if not valid_ids(raw) and not abstained:
        retries.append("no_valid_citations")
        raw = await _ask(ctx, plan, history, ev, notify, RETRY_NOTE)  # one corrective retry
        abstained = raw.get("status") == "insufficient_evidence"
    elif wrong_language(plan.question, str(raw.get("answer", "")), plan.answer_language):
        retries.append("wrong_language")
        retry = await _ask(
            ctx, plan, history, ev, notify, language_reminder(plan.answer_language)
        )  # one retry, kept if valid
        if valid_ids(retry) and not wrong_language(
            plan.question, str(retry.get("answer", "")), plan.answer_language
        ):
            raw, abstained = retry, retry.get("status") == "insufficient_evidence"
    await notify("validating", "Checking the citations")
    out = Generated(retries=retries)
    cited = valid_ids(raw)
    if set(raw.get("citation_ids", [])) - set(by_alias):
        out.warnings.append("Model returned unknown citation IDs; these were discarded")
    text = strip_document_names(
        str(raw.get("answer", "")).strip(), [s.document_name for s in ev.sources]
    )
    if not cited:
        if abstained and text:
            out.text = NO_EVIDENCE if _META.search(text) else text
        elif not raw.get("citation_ids"):
            out.text = "The local model did not provide verifiable citations for this question."
            out.warnings.append(
                "Retrieved source evidence, but the model omitted citation IDs; its answer was not "
                "published. This does not establish that the documents lack the requested fact. "
                "Try rephrasing the question."
            )
        else:
            out.warnings.append(
                "Answer suppressed because no valid supporting citations were returned"
            )
        return out
    if not text:
        out.warnings.append("The model returned an empty answer; try rephrasing the question")
        out.text = "The local model returned an empty answer for this question."
        return out
    if ctx.settings.verify_claims and not abstained:
        await notify("verifying", "Checking that the passages state every sentence")
        text, dropped = await _ground(ctx, text, ev.sources, out, plan.question)
        # one rewrite without them, checked again; what still fails is removed. A long answer is
        # only cut: rewriting it costs a whole new generation.
        if dropped and len(split_statements(text)) <= MAX_REVISED_STATEMENTS:
            out.retries.append("unsupported_statements")
            revised = await _ask(ctx, plan, history, ev, notify, revise_note(dropped))
            again = strip_document_names(
                str(revised.get("answer", "")).strip(), [s.document_name for s in ev.sources]
            )
            if again and revised.get("status") != "insufficient_evidence" and valid_ids(revised):
                text, dropped = await _ground(ctx, again, ev.sources, out, plan.question)
                cited = valid_ids(revised)
        if dropped:
            out.warnings.append(
                f"Removed {len(dropped)} statement(s) that the documents do not state: "
                + " | ".join(dropped)
            )
        if not any(states_something(t) for t in split_statements(text)):
            out.text = NO_EVIDENCE
            out.warnings.append("Nothing in the answer was supported by the documents")
            return out
    if ctx.settings.verify_citations and not abstained:
        await notify("verifying", "Matching each statement to the passages that state it")
        statements = split_statements(text)
        try:
            support = await _attribute(ctx, statements, ev.sources, set(by_alias))
        except ValueError:  # malformed or oversized verification: keep the model's citations
            support = []
            out.warnings.append("Statements could not be checked against the passages")
        cited = list(dict.fromkeys([*cited, *(a for found in support for a in found)]))
        out.statements = [
            {"text": t, "sources": [by_alias[a].chunk_id for a in found]}
            for t, found in zip(statements, support, strict=False)
        ]
    out.citations = ctx.store.chunks.citations([(by_alias[a].chunk_id, None) for a in cited])
    notices = [
        ctx.settings.label_notices[label]
        for label in ctx.settings.label_notices
        if any(label in by_alias[a].labels for a in cited)
    ]
    out.text = "\n\n".join([*notices, text]) if notices else text
    out.status = (
        "insufficient_evidence"
        if abstained
        else "conflicting_evidence"
        if raw.get("status") == "conflicting_evidence"
        else "answered"
    )
    return out
