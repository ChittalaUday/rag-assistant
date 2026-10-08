"""Deterministic stand-ins for the local models.

Embeddings hash words into 384 buckets, so texts sharing words are near each other: enough for
retrieval tests to be meaningful without a model server. Generation returns canned structured
output chosen by which JSON schema the pipeline asked for.
"""

import hashlib
import math
import re
import time

DIMS = 384


def hash_embedding(text: str) -> list[float]:
    vec = [0.0] * DIMS
    for word in re.findall(r"\w+", text.casefold()):
        vec[int(hashlib.sha1(word.encode()).hexdigest(), 16) % DIMS] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class FakeModel:
    """Extracts 'Mira leads Acme.' and answers by citing the first passage."""

    async def health(self):
        return ["qwen3.5:9b", "all-minilm:l6-v2"]

    async def embed(self, texts):
        return [hash_embedding(t) for t in texts]

    async def generate(self, prompt, schema, max_tokens=None):
        props = schema.get("properties", {})
        if "relations" in props:
            return self.extract(prompt)
        if "citation_ids" in props:
            return self.answer(prompt)
        if "question" in props:
            return {"question": prompt.rsplit("FINAL QUESTION:", 1)[1].strip()}
        raise AssertionError(f"unexpected schema {list(props)}")

    def extract(self, prompt):
        return {
            "entities": [
                {"name": "Acme", "type": "Organization"},
                {"name": "Mira", "type": "Person"},
            ],
            "relations": [
                {
                    "subject": "Mira",
                    "predicate": "leads",
                    "object": "Acme",
                    "phrase": "leads",
                    "quote": "Mira leads Acme.",
                }
            ],
        }

    def answer(self, prompt):
        ids = re.findall(r"CHUNK (S\d+)", prompt)
        return {"answer": "Mira leads Acme.", "citation_ids": ids[:1], "status": "answered"}


def ready(client, id):
    for _ in range(300):
        doc = next(x for x in client.get("/api/documents").json()["documents"] if x["id"] == id)
        if doc["status"] in ["ready", "failed"]:
            return doc
        time.sleep(0.01)
    raise AssertionError("processing timed out")
