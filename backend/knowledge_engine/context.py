import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request

from knowledge_engine.config import Settings
from knowledge_engine.extraction.extractor import Extractor
from knowledge_engine.extraction.ontology import Ontology
from knowledge_engine.llm.gateway import ModelGateway
from knowledge_engine.store import Store


@dataclass
class AppContext:
    """Shared runtime state, created once per app and passed to the pipelines and routes."""

    settings: Settings
    model: ModelGateway
    queue: asyncio.Queue[str]
    ontology: Ontology
    store: Store = field(init=False)  # opened in the app lifespan
    extractor: Extractor = field(init=False)

    def upload_path(self, stored: str) -> Path:
        """Uploads live in data_dir under their hash name. The stored path may be spelled relative
        to another working directory (or reach the same directory by a symlink), so look there."""
        here = self.settings.data_dir / Path(stored).name
        return here if here.exists() else Path(stored)


def get_ctx(request: Request) -> AppContext:
    ctx: AppContext = request.app.state.ctx
    return ctx


Ctx = Annotated[AppContext, Depends(get_ctx)]
