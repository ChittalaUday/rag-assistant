import hashlib
import uuid
from pathlib import Path

from fakes import FakeModel
from knowledge_engine.app import create_context, open_store
from knowledge_engine.config import Settings
from knowledge_engine.pipeline.indexing import process


def make_ctx(tmp_path: Path, model=None, **settings):
    ctx = create_context(Settings(data_dir=tmp_path, **settings), model or FakeModel())
    open_store(ctx)
    return ctx


async def index_text(ctx, name: str, text: str, labels=None) -> str:
    """Add a document and run the indexing pipeline synchronously; returns the document id."""
    data = text.encode()
    digest = hashlib.sha256(data).hexdigest()
    path = ctx.settings.data_dir / (digest + Path(name).suffix)
    path.write_bytes(data)
    id = str(uuid.uuid4())
    ctx.store.documents.add(id, name, digest, str(path), labels or [])
    await process(ctx, id)
    return id
