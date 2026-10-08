"""Document parsing: bytes on disk -> text. PDF/DOCX go through Docling (optional extra)."""

import asyncio
from pathlib import Path

DOCUMENT_SUFFIXES = {".txt", ".md", ".csv", ".pdf", ".docx"}


async def parse(path: Path) -> str:
    if path.suffix.lower() in {".txt", ".md", ".csv"}:
        return path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() not in {".pdf", ".docx"}:
        raise ValueError("Supported files: TXT, Markdown, CSV, PDF and DOCX")

    def convert() -> str:
        try:
            from docling.document_converter import DocumentConverter
        except ImportError as exc:
            raise ValueError("PDF/DOCX requires Docling: run uv sync --extra documents") from exc
        result: str = DocumentConverter().convert(str(path)).document.export_to_markdown()
        return result

    return await asyncio.to_thread(convert)
