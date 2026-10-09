"""Response time per pipeline stage and question type, from an `answers` run (metrics.stages_ms,
trace.timings, metrics.usage). The first question is reported apart: it also loads the models."""

import statistics
from collections import defaultdict
from typing import Any

STAGES = [
    "routing", "replying", "rewriting", "embedding_query", "searching", "reranking",
    "waiting_for_model", "generating", "validating", "verifying",
]  # fmt: skip


PERCENTILES = (50, 75, 90, 95, 99)
SMALL = 10  # below this many questions, high percentiles only repeat the slowest one


def distribution(values: list[int]) -> dict[str, int]:
    """min, mean, standard deviation, percentiles (inclusive interpolation) and max, in ms."""
    if not values:
        return {}
    out = {"min": min(values), "mean": round(statistics.fmean(values))}
    out["stdev"] = round(statistics.stdev(values)) if len(values) > 1 else 0
    if len(values) >= SMALL:
        cuts = statistics.quantiles(values, n=100, method="inclusive")
        out |= {f"p{p}": round(cuts[p - 1]) for p in PERCENTILES}
    else:
        out["p50"] = round(statistics.median(values))
    out["max"] = max(values)
    return out


def _total(row: dict[str, Any]) -> int:
    return int(row["timings"].get("total_ms", 0))


def _output_tokens(row: dict[str, Any]) -> int:
    usage = ((row.get("metrics") or {}).get("usage") or {}).get("generate") or {}
    return int(usage.get("output_tokens", 0))


def latency_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Distribution of the total and of each stage, overall (questions after the first) and per
    question type; model usage; how time grows with the answer's length; every question."""
    warm = rows[1:]

    def group(members: list[dict[str, Any]]) -> dict[str, Any]:
        stages: dict[str, list[int]] = defaultdict(list)
        for r in members:
            for stage, ms in ((r.get("metrics") or {}).get("stages_ms") or {}).items():
                stages[stage].append(ms)
        return {
            "n": len(members),
            "total_ms": distribution([_total(r) for r in members]),
            "stages_ms": {s: distribution(stages[s]) for s in STAGES if s in stages},
        }

    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in warm:
        by_type[r["type"]].append(r)
    usage: dict[str, dict[str, dict[str, int]]] = {}
    for kind in ("generate", "embed", "rerank"):
        calls = [
            u[kind] for r in warm if (u := (r.get("metrics") or {}).get("usage") or {}).get(kind)
        ]
        if calls:
            usage[kind] = {k: distribution([c[k] for c in calls]) for k in calls[0]}
    # time against answer length (generation + verification output tokens), least squares
    answered = [r for r in warm if _output_tokens(r) and r["type"] != "small-talk"]
    xs, ys = [_output_tokens(r) for r in answered], [_total(r) for r in answered]
    fit = {}
    if len(answered) > 2:
        slope, intercept = statistics.linear_regression(xs, ys)
        fit = {
            "ms_per_output_token": round(slope, 1),
            "ms_at_zero_tokens": round(intercept),
            "correlation": round(statistics.correlation(xs, ys), 3),
        }
    first = rows[0] if rows else {}
    first_usage = (first.get("metrics") or {}).get("usage") or {}
    return {
        "first_question": {
            "total_ms": _total(first) if first else 0,
            "load_ms": {k: v["load_ms"] for k, v in first_usage.items()},
        },
        "all": group(warm),
        "by_type": dict(
            sorted(
                ((t, group(m)) for t, m in by_type.items()),
                key=lambda kv: -kv[1]["total_ms"].get("p50", 0),
            )
        ),
        "usage": usage,
        "length": fit,
        "questions": [
            {
                "id": r["id"],
                "type": r["type"],
                "question": r["question"],
                "total_ms": _total(r),
                "stages_ms": (r.get("metrics") or {}).get("stages_ms") or {},
                "output_tokens": _output_tokens(r),
                "correct": r.get("correct"),
            }
            for r in rows
        ],
    }


def _line(d: dict[str, int]) -> str:
    return "  ".join(f"{k} {v / 1000:.1f}" for k, v in d.items())


def print_report(report: dict[str, Any]) -> None:
    first = report["first_question"]
    total = first["total_ms"] / 1000
    print(f"first question (models loading): {total:.1f} s, load ms {first['load_ms']}")
    a = report["all"]
    print(f"\nall questions after the first (n={a['n']}), seconds:")
    print(f"  {'total':17} {_line(a['total_ms'])}")
    for stage, d in a["stages_ms"].items():
        print(f"  {stage:17} {_line(d)}")
    print("\nper question type, total seconds:")
    for label, g in report["by_type"].items():
        print(f"  {label:17} n={g['n']:2}  {_line(g['total_ms'])}")
    print("\nmodel usage per question:")
    for kind, fields in report["usage"].items():
        for field, d in fields.items():
            print(f"  {kind:8} {field:14} " + "  ".join(f"{k} {v}" for k, v in d.items()))
    if report["length"]:
        print("\ntime against output tokens:", report["length"])
