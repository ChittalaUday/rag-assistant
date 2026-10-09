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


def assess(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Conservative smoke metrics; captured answers still need semantic review."""
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
    cited_chunks = {c["chunk_id"] for c in citations}
    for statement in result.get("statements", []):
        for chunk_id in statement.get("sources", []):
            if chunk_id not in cited_chunks:
                invalid.append(f"Statement source {chunk_id} is not among the citations")
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
    }


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
                record.update(assess(case, result))
                record.update(
                    answer=result["answer"],
                    warnings=result.get("warnings", []),
                    citations=result.get("citations", []),
                    statements=result.get("statements", []),
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
