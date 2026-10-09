"""Command line: run the pipelines without the web app.

python -m knowledge_engine ingest PATH [PATH ...] [--labels a,b]   index files/folders
python -m knowledge_engine reindex [--all]                         rebuild derived data
python -m knowledge_engine export OUT_DIR                          write the passages
python -m knowledge_engine eval ...                                retrieval/answer evaluation
"""

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from knowledge_engine.app import create_context, open_store
from knowledge_engine.config import Settings
from knowledge_engine.context import AppContext
from knowledge_engine.ingestion.parser import DOCUMENT_SUFFIXES
from knowledge_engine.pipeline.indexing import PIPELINE_VERSION, clean_uploads, process
from knowledge_engine.text import clean_labels


def _files(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for p in paths:
        found += sorted(f for f in p.rglob("*") if f.is_file()) if p.is_dir() else [p]
    return [f for f in found if f.suffix.lower() in DOCUMENT_SUFFIXES]


async def _run(ctx: AppContext, ids: list[str]) -> int:
    failed = 0
    for n, id in enumerate(ids, 1):
        await process(ctx, id)
        d = ctx.store.documents.get(id)
        mark = "ok " if d["status"] == "ready" else "FAIL"
        print(f"[{n}/{len(ids)}] {mark} {d['name']}  chunks={d['chunk_count']}", flush=True)
        for w in d["warnings"]:
            print(f"        warning: {w}")
        if d["status"] != "ready":
            failed += 1
            print(f"        error: {d['error']}")
    print(f"index: {ctx.store.chunks.count()} passages")
    return 1 if failed else 0


async def ingest(ctx: AppContext, args: argparse.Namespace) -> int:
    docs, ids = ctx.store.documents, []
    for path in _files(args.paths):
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        existing = docs.find_by_hash(digest)
        if existing:
            if docs.get(existing)["status"] == "ready":
                print(f"skip (already indexed) {path.name}")
                continue
            ids.append(existing)
            continue
        target = ctx.settings.data_dir / (digest + path.suffix.lower())
        target.write_bytes(data)
        id = str(uuid.uuid4())
        docs.add(id, path.name, digest, str(target), clean_labels(args.labels))
        ids.append(id)
    return await _run(ctx, ids)


async def reindex(ctx: AppContext, args: argparse.Namespace) -> int:
    docs = ctx.store.documents
    ids = (
        [d["id"] for d in docs.all()]
        if args.all
        else docs.outdated(PIPELINE_VERSION, ctx.settings.embedding_model)
    )
    print(f"re-indexing {len(ids)} document(s)")
    for id in ids:
        docs.requeue(id)
    code = await _run(ctx, ids)
    clean_uploads(ctx)
    return code


async def export(ctx: AppContext, args: argparse.Namespace) -> int:
    """Pipeline output as JSON Lines: the searchable passages (chunks) of every document."""
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    store = ctx.store
    names = {r["id"]: r["name"] for r in store.documents.all()}

    def write(name: str, rows: list[dict[str, Any]]) -> None:
        with (out / name).open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        print(f"wrote {len(rows):5} rows -> {out / name}")

    chunks = []
    for doc_id, name in names.items():
        for c in store.chunks.for_document(doc_id):
            chunks.append({"id": c["id"], "document": name, "position": c["position"],
                           "heading": c["heading"], "start": c["char_start"], "end": c["char_end"],
                           "text": c["text"]})  # fmt: skip
    write("chunks.jsonl", chunks)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="knowledge_engine", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("ingest", help="index documents (files or folders)")
    p.add_argument("paths", nargs="+", type=Path)
    p.add_argument("--labels", type=lambda s: [x for x in s.split(",") if x], default=[])
    p = sub.add_parser("reindex", help="rebuild the passages and their vectors from stored files")
    p.add_argument("--all", action="store_true", help="every document, not just outdated ones")
    p = sub.add_parser("export", help="write the passages as JSON Lines")
    p.add_argument("out", type=Path)
    sub.add_parser("eval", help="see python -m knowledge_engine.evaluation --help")
    args, _ = parser.parse_known_args()
    if args.command == "eval":
        from knowledge_engine.evaluation.__main__ import main as run_eval

        sys.argv = [sys.argv[0], *sys.argv[2:]]
        run_eval()
        return
    ctx = create_context(Settings())
    open_store(ctx)
    try:
        code = asyncio.run(
            {"ingest": ingest, "reindex": reindex, "export": export}[args.command](ctx, args)
        )
    finally:
        ctx.store.close()
    sys.exit(code)


if __name__ == "__main__":
    main()
