from knowledge_engine.ingestion.chunker import Chunk
from knowledge_engine.ingestion.situate import DOCUMENT_CHARS, situate, window


def chunk(text: str, start: int) -> Chunk:
    return Chunk(position=0, text=text, char_start=start, char_end=start + len(text), heading=None)


class Model:
    def __init__(self):
        self.calls = []

    async def generate(self, prompt, schema, max_tokens=None, think=False, model=None):
        self.calls.append((prompt, model))
        return {"context": "  This passage is about\nAcme's leave policy.  "}


class Cache(dict):
    def put(self, key, payload):
        self[key] = payload


async def test_situate_caches_and_uses_the_given_model():
    model, cache = Model(), Cache()
    passage = chunk("Requests need written approval.", 10)
    first = await situate(
        model, cache, "small:1b", "Leave policy", "Acme leave policy. " * 5, passage
    )
    again = await situate(
        model, cache, "small:1b", "Leave policy", "Acme leave policy. " * 5, passage
    )
    assert first == again == "This passage is about Acme's leave policy."
    assert len(model.calls) == 1 and model.calls[0][1] == "small:1b"
    assert model.calls[0][0].index("<document") < model.calls[0][0].index("<passage>")


def test_long_documents_are_windowed_around_the_passage():
    document = "x" * 30000
    near_end = chunk("tail", 29990)
    shown = window(document, near_end)
    assert len(shown) == DOCUMENT_CHARS and document.endswith(shown)
    assert window("short", near_end) == "short"
