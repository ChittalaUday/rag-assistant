"""python -m knowledge_engine.evaluation {retrieval,answers,rescore}: measure pipeline stages."""

import argparse
import asyncio
import json
import os
from collections import defaultdict
from pathlib import Path

from knowledge_engine.config import REPO_ROOT, Settings
from knowledge_engine.evaluation.answers import rescore, run_cases
from knowledge_engine.evaluation.dataset import load_cases, load_corpus
from knowledge_engine.evaluation.legacy import LegacySystem
from knowledge_engine.evaluation.metrics import evaluate_hits, summarize
from knowledge_engine.evaluation.systems import (
    PipelineSystem,
    index_corpus,
    live_context,
    scratch_context,
)
from knowledge_engine.llm.ollama import Ollama

FIXTURES = REPO_ROOT / "fixtures"
DEFAULT_CORPUS = [FIXTURES / "documents", FIXTURES / "testing", FIXTURES / "archive" / "northstar"]
DEFAULT_QUESTIONS = [
    FIXTURES / "questions.json",
    FIXTURES / "testing-questions.json",
    FIXTURES / "archive" / "northstar" / "questions.json",
]
ANSWER_QUESTIONS = [
    *DEFAULT_QUESTIONS,
    FIXTURES / "multihop-questions.json",
    FIXTURES / "paraphrase-questions.json",
]


async def run_retrieval(args: argparse.Namespace) -> dict[str, object]:
    corpus = load_corpus([Path(p) for p in args.corpus])
    cases = [c for c in load_cases([Path(p) for p in args.questions]) if c.docs]
    settings = Settings()
    report: dict[str, object] = {"documents": len(corpus), "questions": len(cases), "systems": {}}

    async def measure(name: str, system: object) -> None:
        for k in args.k:
            by_type: dict[str, list[dict[str, float]]] = defaultdict(list)
            for case in cases:
                hits = await system.retrieve(case, k)  # type: ignore[attr-defined]
                by_type[case.type].append(evaluate_hits(hits, case.docs, case.keywords))
            everything = [r for rows in by_type.values() for r in rows]
            report["systems"][f"{name}@{k}"] = {  # type: ignore[index]
                "overall": summarize(everything),
                **{t: summarize(rows) for t, rows in sorted(by_type.items())},
            }

    if args.live_db:  # compare variants on an index that already exists; nothing is written
        async with live_context(settings, args.live_db) as ctx:
            print(f"index: {ctx.store.chunks.count()} chunks")
            for mode in [m for m in args.systems if m != "legacy"]:
                await measure(mode, PipelineSystem(ctx, mode))
        return report
    if "legacy" in args.systems:
        legacy = LegacySystem(Ollama(settings))
        await legacy.build(corpus)
        await measure("legacy", legacy)
    modes = [m for m in args.systems if m != "legacy"]
    if modes:
        async with scratch_context(settings) as ctx:
            await index_corpus(ctx, corpus)
            for mode in modes:
                await measure(mode, PipelineSystem(ctx, mode))
    return report


async def run_answers(args: argparse.Namespace) -> dict[str, object]:
    """Index the corpus in a scratch schema and ask every question through the query pipeline."""
    for pair in args.set:  # setting overrides, e.g. embedding_model=nomic-embed-text
        key, _, value = pair.partition("=")
        os.environ[f"KE_{key.upper()}"] = value
    settings = Settings()
    corpus = load_corpus([Path(p) for p in args.corpus])
    cases = load_cases([Path(p) for p in args.questions])[: args.limit]
    async with scratch_context(settings) as ctx:
        start = asyncio.get_running_loop().time()
        await index_corpus(ctx, corpus)
        indexing = round(asyncio.get_running_loop().time() - start, 1)
        report = await run_cases(ctx, cases, verbose=True)
    report["config"] = {"settings": args.set, "indexing_s": indexing}
    return report


def main() -> None:
    parser = argparse.ArgumentParser(prog="knowledge_engine.evaluation")
    sub = parser.add_subparsers(dest="stage", required=True)
    r = sub.add_parser("retrieval", help="compare retrieval systems (no LLM needed)")
    r.add_argument("--corpus", nargs="+", default=[str(p) for p in DEFAULT_CORPUS])
    r.add_argument("--questions", nargs="+", default=[str(p) for p in DEFAULT_QUESTIONS])
    r.add_argument(
        "--systems",
        nargs="+",
        default=["legacy", "dense", "sparse", "hybrid", "rerank"],
        choices=["legacy", "dense", "sparse", "hybrid", "rerank"],
    )
    r.add_argument("--live-db", help="evaluate on this populated database instead of indexing")
    r.add_argument("--k", nargs="+", type=int, default=[6])
    r.add_argument("--out", type=Path)
    a = sub.add_parser("answers", help="end-to-end answers through the query pipeline")
    a.add_argument("--corpus", nargs="+", default=[str(p) for p in DEFAULT_CORPUS])
    a.add_argument("--questions", nargs="+", default=[str(p) for p in ANSWER_QUESTIONS])
    a.add_argument("--set", nargs="*", default=[], help="setting overrides KEY=VALUE")
    a.add_argument("--limit", type=int)
    a.add_argument("--out", type=Path)
    ch = sub.add_parser("chunking", help="compare chunkers (engine and Chonkie) on retrieval")
    ch.add_argument("--long-docs", type=Path, default=Path("work/chunk-bench/docs"))
    ch.add_argument(
        "--kinds",
        nargs="+",
        default=["structure", "hybrid", "chonkie-recursive", "chonkie-semantic"],
        choices=["structure", "hybrid", "chonkie-recursive", "chonkie-semantic"],
    )
    ch.add_argument("--k", type=int, default=5)
    ch.add_argument("--out", type=Path, default=Path("work/chunk-bench/results.json"))
    rs = sub.add_parser("rescore", help="score saved answers again with the current questions")
    rs.add_argument("results", nargs="+", type=Path, help="JSON files written by `answers`")
    rs.add_argument("--questions", nargs="+", default=[str(p) for p in ANSWER_QUESTIONS])
    args = parser.parse_args()
    if args.stage == "chunking":
        from knowledge_engine.evaluation.chunking import benchmark

        dirs = [*DEFAULT_CORPUS, FIXTURES / "multilingual"]
        results = asyncio.run(
            benchmark(Settings(), dirs, args.long_docs, args.kinds, args.k, args.long_docs.parent)
        )
        args.out.write_text(json.dumps(results, ensure_ascii=False, indent=1))
        return
    if args.stage == "rescore":
        cases = load_cases([Path(p) for p in args.questions])
        for path in args.results:
            result = rescore(json.loads(path.read_text())["results"], cases)
            overall = result["summary"]["overall"]
            print(f"{path.name:32} " + "  ".join(f"{k}={v}" for k, v in overall.items()))
        return
    if args.stage == "answers":
        result = asyncio.run(run_answers(args))
        for group, m in result["summary"].items():
            print(f"  {group:11} " + "  ".join(f"{k}={v}" for k, v in m.items()))
        if args.out:
            args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        return
    report = asyncio.run(run_retrieval(args))
    print(f"{report['documents']} documents, {report['questions']} answerable questions")
    for system, groups in report["systems"].items():  # type: ignore[attr-defined]
        print(f"\n{system}")
        for group, m in groups.items():
            print(f"  {group:10} " + "  ".join(f"{k}={v:.3f}" for k, v in m.items()))
    if args.out:
        args.out.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
