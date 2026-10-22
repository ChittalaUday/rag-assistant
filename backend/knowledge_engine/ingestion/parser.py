"""Document parsing: bytes on disk -> text. Docling handles PDF, DOCX, PPTX, XLSX, HTML and
images (PNG/JPG/WEBP/etc. via OCR). Plain text formats are read directly."""

import asyncio
from pathlib import Path

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".gif"}
_DOCLING_SUFFIXES = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm"} | IMAGE_SUFFIXES
DOCUMENT_SUFFIXES = {".txt", ".md", ".csv"} | _DOCLING_SUFFIXES


async def parse(path: Path) -> str:
    if path.suffix.lower() in {".txt", ".md", ".csv"}:
        return path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() not in _DOCLING_SUFFIXES:
        raise ValueError(
            "Supported: TXT, Markdown, CSV, PDF, DOCX, PPTX, XLSX, HTML, and images (PNG/JPG/WEBP)"
        )

    def convert() -> str:
        try:
            from docling.document_converter import DocumentConverter
        except ImportError as exc:
            raise ValueError("This format requires Docling: run uv sync --extra documents") from exc
        result: str = DocumentConverter().convert(str(path)).document.export_to_markdown()
        return result

    return await asyncio.to_thread(convert)
