"""Run real local-model questions against a running API; no provider substitution."""

import argparse
import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]


def assess(
    case: dict[str, Any], result: dict[str, Any], source_text: dict[str, str] | None = None
) -> dict[str, Any]:
    """Conservative smoke metrics; captured answers still need semantic review.

    source_text maps chunk id -> passage text for the evidence of highlighted graph edges (graph
    evidence carries a reference and a quote, not the passage)."""
    source_text = source_text or {}
    answer = str(result.get("answer", ""))
    expected = case.get("expected_keywords", [])
    found = [word for word in expected if str(word).casefold() in answer.casefold()]
    keyword_coverage = len(found) / len(expected) if expected else 1.0
    citations = result.get("citations", [])
    actual_sources = {c["document_name"].casefold() for c in citations}
    expected_sources = {Path(s).name.casefold() for s in case.get("source_documents", [])}
    source_coverage = (
        len(expected_sources & actual_sources) / len(expected_sources) if expected_sources else 1.0
    )
    invalid: list[str] = []
    for citation in citations:
        if citation.get("quote") and citation["quote"] not in citation["text"]:
            invalid.append(f"Citation {citation['chunk_id']} quote is absent from passage")
    graph = result.get("graph", {})
    selected = set(graph.get("highlighted_edge_ids", []))
    cited_chunks = {c["chunk_id"] for c in citations}
    for edge in graph.get("edges", []):
        if edge["id"] not in selected:
            continue
        evidence = edge.get("evidence", [])
        if not evidence:
            invalid.append(f"Highlighted edge {edge['id']} has no evidence")
        if not any(c["chunk_id"] in cited_chunks for c in evidence):
            invalid.append(f"Highlighted edge {edge['id']} has no answer citation")
        for citation in evidence:
            passage = source_text.get(citation["chunk_id"], "")
            if not citation.get("quote") or citation["quote"] not in passage:
                invalid.append(f"Edge {edge['id']} quote is absent from passage")
    unknown = case.get("type") in {"unknown", "unanswerable", "abstention"}
    abstained = bool(
        re.search(
            r"\b(not (?:provided|available|specified|documented|found|established|disclosed)|"
            r"(?:do|does|is) not (?:contain|mention|specify|provide|established|disclose)|"
            r"(?:don.t|cannot|can.t|could not) (?:find|determine|answer)|"
            r"no (?:supporting|relevant|evidence|information|public source)|"
            r"insufficient (?:evidence|information))\b",
            answer,
            re.IGNORECASE,
        )
    )
    passed = (
        abstained if unknown else keyword_coverage == 1.0 and source_coverage == 1.0
    ) and not invalid
    return {
        "smoke_pass": passed,
        "keyword_coverage": keyword_coverage,
        "missing_keywords": [w for w in expected if w not in found],
        "source_coverage": source_coverage,
        "missing_sources": sorted(expected_sources - actual_sources),
        "invalid_evidence": invalid,
        "abstained": abstained,
        "highlighted_edges": len(selected),
    }


async def highlighted_sources(client: httpx.AsyncClient, result: dict[str, Any]) -> dict[str, str]:
    """Passage text for the evidence of the highlighted edges, fetched from /sources."""
    graph = result.get("graph", {})
    selected = set(graph.get("highlighted_edge_ids", []))
    chunk_ids = {
        c["chunk_id"] for e in graph.get("edges", []) if e["id"] in selected for c in e["evidence"]
    }
    texts: dict[str, str] = {}
    for chunk_id in sorted(chunk_ids):
        response = await client.get(f"/api/sources/{chunk_id}")
        texts[chunk_id] = "" if response.is_error else response.json()["text"]
    return texts


async def run(args: argparse.Namespace) -> int:
    cases: list[dict[str, Any]] = []
    for path in args.questions:
        loaded = json.loads(path.read_text())
        for case in loaded if isinstance(loaded, list) else loaded["questions"]:
            # Namespace ids by file so follow-up chains cannot collide across question sets.
            case = {**case, "id": f"{path.stem}:{case['id']}"}
            if case.get("followup_of"):
                case["followup_of"] = f"{path.stem}:{case['followup_of']}"
            cases.append(case)
    if args.limit:
        cases = cases[: args.limit]
    results: list[dict[str, Any]] = []
    conversation_ids: dict[str, str] = {}
    async with httpx.AsyncClient(base_url=args.api, timeout=300) as client:
        health = (await client.get("/api/health")).raise_for_status().json()
        if not health["ollama_available"]:
            raise RuntimeError("Ollama is unavailable; this evaluator requires real models")
        documents = (await client.get("/api/documents")).raise_for_status().json()["documents"]
        if not documents or any(d["status"] != "ready" for d in documents):
            raise RuntimeError("Load and finish processing the sample documents before evaluating")
        for index, case in enumerate(cases, 1):
            start = time.perf_counter()
            request: dict[str, Any] = {"question": case["question"]}
            parent = case.get("followup_of")
            if parent:
                if parent not in conversation_ids:
                    raise RuntimeError(f"Follow-up {case['id']} needs prior case {parent}")
                request["conversation_id"] = conversation_ids[parent]
            response = await client.post("/api/chat", json=request)
            elapsed = round(time.perf_counter() - start, 3)
            record: dict[str, Any] = {
                "id": case["id"],
                "question": case["question"],
                "seconds": elapsed,
            }
            if response.is_error:
                record.update({"smoke_pass": False, "error": response.text})
            else:
                result = response.json()
                conversation_ids[case["id"]] = result["conversation_id"]
                record.update(assess(case, result, await highlighted_sources(client, result)))
                record.update(
                    answer=result["answer"],
                    warnings=result.get("warnings", []),
                    citations=result.get("citations", []),
                    highlighted_edge_ids=result.get("graph", {}).get("highlighted_edge_ids", []),
                )
            results.append(record)
            label = "PASS" if record["smoke_pass"] else "REVIEW"
            print(f"{index}/{len(cases)} {case['id']}: {label} ({elapsed}s)", flush=True)
            output = {
                "provider": "real local Ollama",
                "health": health,
                "metric": "Expected-term/source coverage smoke check, not semantic correctness",
                "passed": sum(r["smoke_pass"] for r in results),
                "total": len(results),
                "results": results,
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
    print(f"Saved real-model observations to {args.output}")
    return 0 if all(r["smoke_pass"] for r in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--questions", nargs="+", type=Path, default=[ROOT / "fixtures/questions.json"]
    )
    parser.add_argument("--output", type=Path, default=ROOT / "docs/reports/live-evaluation.json")
    raise SystemExit(asyncio.run(run(parser.parse_args())))
