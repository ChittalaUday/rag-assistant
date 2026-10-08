"""python -m knowledge_engine.evaluation {retrieval,extraction} — measure pipeline stages."""

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path

from knowledge_engine.config import REPO_ROOT, Settings
from knowledge_engine.evaluation.dataset import load_cases, load_corpus
from knowledge_engine.evaluation.extraction import FileCache, evaluate_document, score
from knowledge_engine.evaluation.legacy import LegacySystem
from knowledge_engine.evaluation.metrics import evaluate_hits, summarize
from knowledge_engine.evaluation.systems import (
    PipelineSystem,
    index_corpus,
    live_context,
    scratch_context,
)
from knowledge_engine.extraction.extractor import Extractor
from knowledge_engine.extraction.ontology import load_ontology
from knowledge_engine.llm.gateway import ModelGateway
from knowledge_engine.llm.ollama import Ollama

FIXTURES = REPO_ROOT / "fixtures"
DEFAULT_CORPUS = [FIXTURES / "documents", FIXTURES / "testing", FIXTURES / "archive" / "northstar"]
DEFAULT_QUESTIONS = [
    FIXTURES / "questions.json",
    FIXTURES / "testing-questions.json",
    FIXTURES / "archive" / "northstar" / "questions.json",
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
            print(f"index: {ctx.store.graph.stats()}, {ctx.store.chunks.count()} chunks")
            for mode in [m for m in args.systems if m != "legacy"]:
                await measure(mode, PipelineSystem(ctx, mode))
        return report
    if "legacy" in args.systems:
        legacy = LegacySystem(Ollama(settings))
        await legacy.build(corpus)
        await measure("legacy", legacy)
    text_only = [m for m in args.systems if m in {"dense", "sparse", "hybrid", "rerank"}]
    if text_only:
        async with scratch_context(settings) as ctx:
            await index_corpus(ctx, corpus, extract=False)
            for mode in text_only:
                await measure(mode, PipelineSystem(ctx, mode))
    if "graph" in args.systems:
        cache = FileCache(args.cache)
        async with scratch_context(settings, cache) as ctx:
            await index_corpus(ctx, corpus, extract=True)
            print(f"graph: {ctx.store.graph.stats()}")
            await measure("graph", PipelineSystem(ctx, "graph"))
    return report


async def run_extraction(args: argparse.Namespace) -> None:
    settings = Settings()
    ontology = load_ontology(settings.ontology_path)
    gateway = ModelGateway(Ollama(settings), settings.model_timeout)
    cache = FileCache(args.cache) if args.cache else None
    extractor = Extractor(gateway, ontology, settings.chat_model, cache)
    gold = json.loads(args.gold.read_text())["documents"]
    corpus = load_corpus([Path(p) for p in args.corpus])
    totals = {"gold": 0, "pair": 0, "triple": 0, "extracted": 0, "in_gold": 0}
    for name, triples in gold.items():
        if args.only and name not in args.only:
            continue
        if name not in corpus:
            print(f"! {name} not in the corpus folders")
            continue
        result, _ = await evaluate_document(extractor, ontology, name, corpus[name], triples)
        print(
            f"\n{name}: gold {result.gold}  pair-recall {result.pair_hits}/{result.gold}  "
            f"triple-recall {result.triple_hits}/{result.gold}  "
            f"extracted {result.extracted} ({result.extracted_in_gold} match gold pairs)"
        )
        if args.verbose:
            for line in result.missed:
                print("   MISS ", line)
            for line in result.extra:
                print("   extra", line)
        totals["gold"] += result.gold
        totals["pair"] += result.pair_hits
        totals["triple"] += result.triple_hits
        totals["extracted"] += result.extracted
        totals["in_gold"] += result.extracted_in_gold
    g, e = totals["gold"] or 1, totals["extracted"] or 1
    print(
        f"\nTOTAL gold={totals['gold']}  pair-recall={totals['pair'] / g:.3f}  "
        f"triple-recall={totals['triple'] / g:.3f}  extracted={totals['extracted']}  "
        f"pair-precision-vs-gold={totals['in_gold'] / e:.3f} (lower bound: gold is not exhaustive)"
    )


def run_graph(args: argparse.Namespace) -> None:
    """Score any running server's /api/graph (old or new pipeline) against the gold relations."""
    import httpx

    graph = httpx.get(f"{args.api}/api/graph", timeout=60).raise_for_status().json()
    labels = {n["id"]: n["label"] for n in graph["nodes"]}
    gold = json.loads(args.gold.read_text())["documents"]
    totals = {"gold": 0, "pair": 0, "triple": 0, "extracted": 0, "in_gold": 0}
    for name, triples in gold.items():
        found = [
            (labels[e["source"]], e.get("predicate"), labels[e["target"]])
            for e in graph["edges"]
            if any(ev["document_name"] == name for ev in e["evidence"])
        ]
        r = score(found, triples)
        print(
            f"{name:34} gold {r.gold:3}  pair-recall {r.pair_hits}/{r.gold}  "
            f"triple-recall {r.triple_hits}/{r.gold}  extracted {r.extracted} "
            f"({r.extracted_in_gold} match)"
        )
        for key, value in zip(
            totals,
            (r.gold, r.pair_hits, r.triple_hits, r.extracted, r.extracted_in_gold),
            strict=True,
        ):
            totals[key] += value
    g, e = totals["gold"] or 1, totals["extracted"] or 1
    print(
        f"\nTOTAL gold={totals['gold']} pair-recall={totals['pair'] / g:.3f} "
        f"triple-recall={totals['triple'] / g:.3f} extracted={totals['extracted']} "
        f"pair-precision-vs-gold={totals['in_gold'] / e:.3f}\n"
        f"graph size: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges"
    )


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
        choices=[
            "legacy",
            "dense",
            "sparse",
            "hybrid",
            "rerank",
            "graph",
            "nograph",
            "graphlist",
            "entityfeature",
        ],
    )
    r.add_argument("--live-db", help="evaluate on this populated database instead of indexing")
    r.add_argument("--cache", type=Path, default=Path("work/extract-cache.json"))
    r.add_argument("--k", nargs="+", type=int, default=[6])
    r.add_argument("--out", type=Path)
    x = sub.add_parser("extraction", help="entity/relation recall against hand-labelled gold")
    x.add_argument("--corpus", nargs="+", default=[str(p) for p in DEFAULT_CORPUS])
    x.add_argument("--gold", type=Path, default=FIXTURES / "gold" / "relations.json")
    x.add_argument("--cache", type=Path, help="JSON file caching raw model output")
    x.add_argument("--only", nargs="+", help="evaluate only these document names")
    x.add_argument("-v", "--verbose", action="store_true")
    g = sub.add_parser(
        "graph", help="score a running server's /api/graph against the gold relations"
    )
    g.add_argument("--api", required=True, help="base URL of the server to read (a scratch one)")
    g.add_argument("--gold", type=Path, default=FIXTURES / "gold" / "relations.json")
    args = parser.parse_args()
    if args.stage == "graph":
        run_graph(args)
        return
    if args.stage == "extraction":
        asyncio.run(run_extraction(args))
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
