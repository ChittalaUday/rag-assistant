"""The original retrieval algorithm, kept only as an evaluation baseline.

Fixed ~650-character sentence chunks, cosine + unweighted lexical overlap computed in Python over
every chunk. New retrieval is measured against this so improvements are reproducible numbers.
"""

import math
import re

from knowledge_engine.evaluation.dataset import Case
from knowledge_engine.evaluation.metrics import Hit
from knowledge_engine.llm.ollama import ModelClient
from knowledge_engine.text import content_terms


def legacy_chunks(text: str) -> list[str]:
    out: list[str] = []
    start = 0
    sentence_ends = [m.end() for m in re.finditer(r"[.!?](?:\s+|$)|\n", text)]
    while start < len(text):
        limit = min(start + 650, len(text))
        end = limit
        if limit < len(text):
            boundaries = [p for p in sentence_ends if start + 200 <= p <= limit]
            if boundaries:
                end = boundaries[-1]
            else:
                whitespace = text.rfind(" ", start + 200, limit)
                if whitespace > start:
                    end = whitespace + 1
        out.append(text[start:end])
        if end == len(text):
            break
        overlap_starts = [p for p in sentence_ends if max(start + 1, end - 180) <= p < end]
        if overlap_starts:
            next_start = overlap_starts[-1]
        else:
            whitespace = text.find(" ", max(start + 1, end - 90), end)
            next_start = whitespace + 1 if whitespace >= 0 else end
        start = next_start
    return [part for part in out if part.strip()]


class LegacySystem:
    name = "legacy"

    def __init__(self, model: ModelClient):
        self.model = model
        self.rows: list[tuple[str, str, list[float]]] = []  # (doc, text, embedding)

    async def build(self, corpus: dict[str, str]) -> None:
        for doc, text in corpus.items():
            pieces = legacy_chunks(text)
            vectors: list[list[float]] = []
            for i in range(0, len(pieces), 16):
                vectors.extend(await self.model.embed(pieces[i : i + 16]))
            self.rows += [(doc, p, v) for p, v in zip(pieces, vectors, strict=True)]

    async def retrieve(self, case: Case, k: int) -> list[Hit]:
        # The original concatenated recent history with the question and embedded the tail.
        query = "\n".join(case.history[-4:])[-3000:] + "\n" + case.question
        vector = (await self.model.embed([query[-650:]]))[0]
        terms = content_terms(query)

        def score(row: tuple[str, str, list[float]]) -> float:
            other = row[2]
            cosine = sum(a * b for a, b in zip(vector, other, strict=True)) / max(
                math.sqrt(sum(a * a for a in vector) * sum(b * b for b in other)), 1e-9
            )
            return cosine + len(terms & content_terms(row[1])) / max(len(terms), 1)

        ranked = sorted(self.rows, key=score, reverse=True)
        return [Hit(doc, text) for doc, text, _ in ranked[:k]]
