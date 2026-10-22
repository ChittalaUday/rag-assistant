"""Deterministic stand-ins for the local models.

Embeddings hash words into 384 buckets, so texts sharing words are near each other: enough for
retrieval tests to be meaningful without a model server. Generation returns canned structured
output chosen by which JSON schema the pipeline asked for.
"""

import hashlib
import json
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
    """Answers by citing the first passage."""

    async def health(self):
        return ["qwen3.5:9b", "all-minilm:l6-v2"]

    loaded_names = None  # models "in memory"; None: every installed one
    load_calls = ()

    server_state = None  # the answer model's state when it runs on a model server

    async def chat_state(self):
        return self.server_state

    server_ids = ()  # models the answer server lists

    async def server_models(self):
        return list(self.server_ids)

    async def loaded(self):
        held = await self.health() if self.loaded_names is None else self.loaded_names
        return [{"name": n, "size": 1_000_000, "expires_at": "2030-01-01T00:00:00Z"} for n in held]

    async def load(self, model, embedding=False):
        self.load_calls = [*self.load_calls, (model, embedding)]
        if self.server_state is not None and not embedding:  # the server finished waking
            self.server_state = {"state": "ready"}
        if self.loaded_names is not None:
            self.loaded_names = [*self.loaded_names, model]

    async def embed(self, texts):
        return [hash_embedding(t) for t in texts]

    context = 2048  # the fake embedding model's input window

    async def embedding_context(self):
        return self.context

    unsupported = ()  # claims containing any of these words are "not supported" by the verifier

    async def count_tokens(self, text):
        return len(text.encode()) // 3  # deterministic stand-in for a tokenizer

    async def generate(
        self, prompt, schema, max_tokens=None, think=False, model=None, on_text=None
    ):
        props = schema.get("properties", {})
        if "citation_ids" in props:
            value = self.answer(prompt)
            if on_text:  # stream the JSON in two pieces, as a streaming model would
                output = json.dumps(value)
                await on_text(output[: len(output) // 2])
                await on_text(output)
            return value
        if "question" in props:
            return {"question": prompt.rsplit("FINAL QUESTION:", 1)[1].strip()}
        if "reply" in props:  # a reply to a message the embeddings routed to small talk
            return {"reply": "Hello! Ask me about your documents."}
        if "unsupported" in props:  # claim check: the numbered statements with a flagged word
            texts = re.findall(r"^(\d+)\. (.*)$", prompt.split("STATEMENTS:")[1], re.M)
            return {
                "unsupported": [int(n) for n, t in texts if any(w in t for w in self.unsupported)]
            }
        if "statements" in props:  # attribution: every statement rests on the first passage
            count = props["statements"]["maxItems"]
            first = re.findall(r"^(S\d+):$", prompt, re.M)[:1]
            return {"statements": [{"n": n, "sources": first} for n in range(1, count + 1)]}
        raise AssertionError(f"unexpected schema {list(props)}")

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
