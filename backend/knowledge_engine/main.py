"""ASGI entrypoint: ``uvicorn knowledge_engine.main:app``."""

from knowledge_engine.app import create_app

app = create_app()
