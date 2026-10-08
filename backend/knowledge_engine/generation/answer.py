"""Answer generation and validation: cite or abstain.

The model proposes an answer and the passages that support it; everything else is derived from
those citations by code: which graph relations to highlight, whether the evidence conflicts, what
provenance notices apply. An answer without a valid citation is never published silently.
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from knowledge_engine.context import AppContext
from knowledge_engine.generation.prompt import build_prompt
from knowledge_engine.reasoning.evidence import Evidence
from knowledge_engine.retrieval.models import QueryPlan

Notify = Callable[[str, str], Awaitable[None]]
NO_EVIDENCE = "I could not find supporting evidence in the uploaded documents."
RETRY_NOTE = (
    "REMINDER: your previous reply cited no valid passage. Cite the SOURCE aliases (S1, S2, ...) "
    "that support each statement, or set status to insufficient_evidence if nothing supports it."
)


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
        text = re.sub(rf"(?i)\s*[(\[]\s*{full}\s*[)\]]", "", text)
    # a removed lead-in that opened a sentence leaves the next word to be capitalised
    text = re.sub(r"(^|(?<=[.!?]\s)|(?<=\n))\x00(\w)", lambda m: m[1] + m[2].upper(), text)
    return text.replace("\x00", "").strip()


@dataclass
class Generated:
    text: str = NO_EVIDENCE
    status: str = "insufficient_evidence"  # answered | insufficient_evidence | conflicting_evidence
    citations: list[dict[str, Any]] = field(default_factory=list)
    relation_ids: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


async def _ask(
    ctx: AppContext,
    plan: QueryPlan,
    history: list[dict[str, Any]],
    ev: Evidence,
    notify: Notify,
    extra: str,
) -> dict[str, Any]:
    prompt, schema = build_prompt(plan, history, ev, extra)
    if ctx.model.lock.locked():
        await notify("waiting_for_model", "Waiting for the local answer model")

    async def started() -> None:
        await notify("generating", "Generating an answer from the selected source passages")

    raw = await ctx.model.generate(prompt, schema, started)
    if "citation_ids" not in raw:
        raise ValueError("Model omitted required citation_ids; answer was not published")
    return raw


async def generate_answer(
    ctx: AppContext, plan: QueryPlan, history: list[dict[str, Any]], ev: Evidence, notify: Notify
) -> Generated:
    if not ev.sources:
        return Generated()
    raw = await _ask(ctx, plan, history, ev, notify, "")
    by_alias = {s.alias: s for s in ev.sources}

    def valid_ids(r: dict[str, Any]) -> list[str]:
        return list(dict.fromkeys(a for a in r.get("citation_ids", []) if a in by_alias))

    abstained = raw.get("status") == "insufficient_evidence"
    if not valid_ids(raw) and not abstained:
        raw = await _ask(ctx, plan, history, ev, notify, RETRY_NOTE)  # one corrective retry
        abstained = raw.get("status") == "insufficient_evidence"
    await notify("validating", "Checking citations and relationship evidence")
    out = Generated()
    cited = valid_ids(raw)
    if set(raw.get("citation_ids", [])) - set(by_alias):
        out.warnings.append("Model returned unknown citation IDs; these were discarded")
    text = strip_document_names(
        str(raw.get("answer", "")).strip(), [s.document_name for s in ev.sources]
    )
    if not cited:
        if abstained and text:
            out.text = text
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
    out.citations = ctx.store.chunks.citations([(by_alias[a].chunk_id, None) for a in cited])
    notices = [
        ctx.settings.label_notices[label]
        for label in ctx.settings.label_notices
        if any(label in by_alias[a].labels for a in cited)
    ]
    out.text = "\n\n".join([*notices, text]) if notices else text
    out.status = "insufficient_evidence" if abstained else "answered"
    answer_l = text.casefold()
    linked = {e.id for e in plan.entities}

    def mentioned(name: str) -> bool:  # whole words: "Al" is not in "also"
        return re.search(rf"(?<!\w){re.escape(name.casefold())}(?!\w)", answer_l) is not None

    out.relation_ids = {
        f.relation_id
        for f in ev.facts
        if set(f.aliases) & set(cited)
        and (
            (mentioned(f.subject) and mentioned(f.object))
            or (mentioned(f.subject) and f.object_id in linked)
            or (mentioned(f.object) and f.subject_id in linked)
        )
    }
    cited_relations = {f.relation_id for f in ev.facts if set(f.aliases) & set(cited)}
    if any(set(c.relation_ids) & cited_relations for c in ev.conflicts) and not abstained:
        out.status = "conflicting_evidence"
    return out
