#!/usr/bin/env python3
"""
Interactive single-file dashboard (embedded JSON + vanilla JS, no external libs) from a run_suite.py results dir.
  python benchmark_tools/build_dashboard.py <results_dir> [--out FILE]
Default out: benchmark_tools/outputs/spark_report/DASHBOARD.html
"""

import argparse
import csv
import json
import re
from pathlib import Path

import build_report as br

HERE = Path(__file__).parent
REPLACE = (("NVIDIA-", ""), ("Meta-", ""), ("-Instruct-2507", "-2507"), ("-Instruct", ""), ("-it-quantized", ""),
           ("-it", ""), ("nemotron-cascade-2", "nemotron-cascade"))


def pretty(model):
    m = model.split("/")[-1]
    for a, b in REPLACE:
        m = m.replace(a, b)
    return m


SMALL = re.compile(r"(_small|_nv4s)$|^ollama_.*_new$|^ollama_qwen3_0p6b|^vllm_qwen3_4b")


def fmt_of(spec):
    m, kind = spec["model"], spec["kind"]
    if "NVFP4" in m:
        return "NVFP4"
    if "FP8" in m:
        return "FP8"
    if "mxfp4" in m.lower():
        return "MXFP4"
    if "w4a4" in m.lower() or "fp4" in m.lower():
        return "FP4"
    if "w4a16" in m:
        return "W4A16"
    if "gpt-oss" in m:
        return "MXFP4"
    return "GGUF" if kind in ("ollama", "llamacpp") else "bf16"


def timeline(results, name, phases):
    p = results / name / "hw.csv"
    if not p.exists():
        return None
    rows = list(csv.DictReader(open(p)))
    if not rows:
        return None
    t0 = float(rows[0]["ts"])
    step = max(1, len(rows) // 220)
    pts = []
    for r in rows[::step]:
        try:
            pts.append([round(float(r["ts"]) - t0), round(float(r["power_w"]), 1), round(float(r["mem_used_mb"]) / 1024, 1),
                        round(float(r["gpu_util"]))])
        except ValueError:
            pass
    ph = [{"n": b, "a": round(float(c) - t0), "b": round(float(d) - t0)} for a, b, c, d in phases if a == name]
    return {"pts": pts, "ph": ph}


def build(results, out):
    runs = [r for r in br.collect(results) if r["bench"]]
    phases = [l.split(",") for l in (results / "phases.csv").read_text().splitlines() if l.strip()]
    models = []
    for r in runs:
        s = r["spec"]
        b = {}
        for prof, d in r["bench"].items():
            b[prof] = {str(c): {"agg": x["token_throughput_tps"], "per": x["mean_gen_tps"], "p50": x["p50_latency"],
                                "p95": x["p95_latency"], "t50": x["p50_ttft"], "t95": x["p95_ttft"], "rps": x["throughput_rps"],
                                "ok": x["success_rate"]} for c, x in d.items()}
        hw = {}
        for prof, h in r["hw"].items():
            if h:
                hw[prof] = {k: [round(v, 1) if v is not None else None for v in vals] for k, vals in h.items()}
        acc = None
        if "acc_n" in r:
            acc = {"rule": r["acc_rule"], "n": r["acc_n"], "judge": r["acc_judge"][0] if r["acc_judge"] else None,
                   "lat": r["acc_lat"], "cats": r["cats"],
                   "fails": [{"id": x["id"], "cat": x["cat"], "a": (x["answer"] or "")[:160], "why": "; ".join(x["rule_why"])[:120],
                              "jp": x.get("judge_pass"), "jr": (x.get("judge_reason") or "")[:140]} for x in r["acc_fail"]]}
        g = lambda p, c, k: ((r["bench"].get(p, {}).get(c) or {}).get(k))
        hg = (r["hw"].get("gen") or {})
        power = hg.get("power_w", [None])[0]
        agg1, agg4 = g("gen", 1, "token_throughput_tps"), g("gen", 4, "token_throughput_tps")
        m = {"id": r["name"], "name": pretty(s["model"]), "full": s["model"], "engine": s["kind"], "fmt": fmt_of(s),
             "tuned": r["name"].endswith("_tuned"), "small": bool(SMALL.search(r["name"])), "note": s.get("note", ""), "think": s.get("think"),
             "cfg": " ".join(s.get("args", [])) if s["kind"] == "vllm" else " ".join(f"{k}={v}" for k, v in s.get("env", {}).items()),
             "bench": b, "hw": hw, "acc": acc, "tl": timeline(results, r["name"], phases),
             "k": {"acc": r["acc_rule"] / r["acc_n"] * 100 if acc else None,
                   "judge": (r["acc_judge"][0] / r["acc_judge"][1] * 100) if acc and r["acc_judge"] else None,
                   "agg1": agg1, "agg4": agg4, "per1": g("gen", 1, "mean_gen_tps"), "per4": g("gen", 4, "mean_gen_tps"),
                   "ttft1": g("rag", 1, "p50_ttft"), "ttft4": g("rag", 4, "p50_ttft"), "ragLat4": g("rag", 4, "p95_latency"),
                   "power": power, "mem": (hg.get("mem_used_mb", [None, None])[1] or 0) / 1024 or None,
                   "scale": agg4 / agg1 if agg1 and agg4 else None, "eff": agg4 / power if agg4 and power else None}}
        det = results / r["name"] / "determinism.json"
        if det.exists():
            m["det"] = json.loads(det.read_text(encoding="utf-8"))
        models.append(m)

    base = [m for m in models if not m["tuned"] and m["k"]["acc"] is not None]
    ok_all = [m for m in base if m["k"]["acc"] >= 90]
    ok = [m for m in ok_all if not m["small"]]  # headline picks compare full-size models; small ones get their own card
    best = lambda lst, key: max(lst, key=lambda m: m["k"][key])
    top_acc = max(m["k"]["acc"] for m in base)
    acc_tied = sorted([m for m in base if m["k"]["acc"] == top_acc and (m["k"]["judge"] or 0) >= 99], key=lambda m: -m["k"]["agg4"])
    quick = [
        {"title": "Best all-round", "id": best(ok, "agg4")["id"], "why": "fastest model with ≥90% accuracy"},
        {"title": "Most accurate", "id": acc_tied[0]["id"], "why": "100% correct; ties: " + ", ".join(m["name"] for m in acc_tied[1:])},
        {"title": "Snappiest on long documents", "id": min((m for m in ok if m["id"] not in (best(ok, "agg4")["id"], acc_tied[0]["id"])),
                                                  key=lambda m: m["k"]["ttft4"])["id"], "why": "shortest wait for the first word on a ~3k-token prompt with 4 users, ≥90% accuracy"},
        {"title": "Best small model", "id": best([m for m in ok_all if m["small"]], "agg4")["id"], "why": "fastest ≤4B model with ≥90% accuracy"},
        {"title": "Most efficient", "id": best(ok, "eff")["id"], "why": "most tokens per joule, ≥90% accuracy"},
        {"title": "Tiny & fast", "id": best(base, "agg4")["id"], "why": "fastest overall — but accuracy is low"},
    ]
    pairs = [{"name": "Qwen3.6-35B-A3B", "a": "vllm_qwen36_baseline", "b": "vllm_qwen36_35b_fp8", "al": "NVFP4"},
             {"name": "Qwen3-30B-A3B", "a": "vllm_qwen3_30b_a3b_nvfp4", "b": "vllm_qwen3_30b_a3b_fp8", "al": "NVFP4"},
             {"name": "Qwen3-8B", "a": "vllm_qwen3_8b_nvfp4", "b": "vllm_qwen3_8b_fp8", "al": "NVFP4"},
             {"name": "Llama-3.1-8B", "a": "vllm_llama31_8b_nvfp4", "b": "vllm_llama31_8b_fp8", "al": "NVFP4"},
             {"name": "Qwen3-4B", "a": "vllm_qwen3_4b_new", "b": "vllm_qwen3_4b_fp8", "al": "bf16"}]
    ids = {m["id"] for m in models}
    pairs = [p for p in pairs if p["a"] in ids and p["b"] in ids]
    note = results / "LLAMACPP_NOTE.txt"
    llama_ids = [m["id"] for m in models if m["engine"] == "llamacpp"]
    llama = {"ref": "vllm_qwen36_baseline", "ids": llama_ids, "note": note.read_text(encoding="utf-8").strip() if note.exists() else "",
             "intro": "llama.cpp (llama-server, 4 slots) running unsloth's UD-Q4_K_XL and MXFP4 GGUFs and a community NVFP4 GGUF of Qwen3.6-35B-A3B."}
    data = {"models": models, "quick": quick, "pairs": pairs, "llama": llama,
            "cats": ["grounded", "math", "logic", "knowledge", "instruction", "extraction", "semantic", "multilingual"]}
    tpl = (HERE / "dashboard_template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(tpl.replace("/*__DATA__*/null", payload), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, {len(models)} models)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--out", default=str(HERE / "outputs" / "spark_report" / "DASHBOARD.html"))
    a = ap.parse_args()
    build(Path(a.results), Path(a.out))
