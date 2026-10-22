#!/usr/bin/env python3
"""
Build a combined Markdown + HTML report from a run_suite.py results dir.
  python benchmark_tools/build_report.py <results_dir> [--out DIR]
Default out: benchmark_tools/outputs/spark_report/
"""

import argparse
import csv
import html
import json
from pathlib import Path

HERE = Path(__file__).parent
PROFILES = ["gen", "rag"]


def load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def f(x, nd=2, dash="–"):
    return dash if x is None else f"{x:.{nd}f}"


def hw_stats(spec_dir, phases, spec, phase):
    """Hardware stats while one benchmark phase ran."""
    try:
        t0, t1 = next((float(a), float(b)) for s, p, a, b in phases if s == spec and p == phase)
        rows = [r for r in csv.DictReader(open(spec_dir / "hw.csv")) if t0 <= float(r["ts"]) <= t1]
    except Exception:
        return None

    def col(k):
        v = []
        for r in rows:
            try:
                v.append(float(r[k]))
            except ValueError:
                pass
        return v

    out = {}
    for k in ["gpu_util", "power_w", "temp_c", "sm_mhz", "mem_used_mb", "cpu_pct"]:
        v = col(k)
        out[k] = (sum(v) / len(v), max(v)) if v else (None, None)
    return out


def collect(results):
    phases = [l.strip().split(",") for l in (results / "phases.csv").read_text().splitlines() if l.strip()] \
        if (results / "phases.csv").exists() else []
    specs = {s["name"]: s for s in load(results / "specs.json", [])}
    runs = []
    for d in sorted(p for p in results.iterdir() if p.is_dir()):
        spec = specs.get(d.name, {"name": d.name, "kind": "?", "model": d.name, "note": ""})
        r = {"name": d.name, "spec": spec, "failed": (d / "FAILED.log").exists(), "bench": {}, "hw": {}}
        acc = load(d / "accuracy.json")
        if acc:
            rows = acc["results"]
            r["acc_n"] = len(rows)
            r["acc_rule"] = sum(x["rule_pass"] for x in rows)
            j = [x for x in rows if x.get("judge_pass") is not None]
            r["acc_judge"] = (sum(x["judge_pass"] for x in j), len(j)) if j else None
            r["acc_lat"] = sum(x["latency"] for x in rows) / len(rows)
            cats = {}
            for x in rows:
                c = cats.setdefault(x["cat"], [0, 0, 0, 0])
                c[0] += 1
                c[1] += x["rule_pass"]
                if x.get("judge_pass") is not None:
                    c[3] += 1
                    c[2] += x["judge_pass"]
            r["cats"] = cats
            r["acc_fail"] = [x for x in rows if not x["rule_pass"] or x.get("judge_pass") is False]
        for prof in PROFILES:
            b = load(d / f"bench_{prof}.json")
            if b:
                r["bench"][prof] = {s["concurrency"]: s for s in b["summaries"]}
                r["hw"][prof] = hw_stats(d, phases, d.name, f"bench_{prof}")
        runs.append(r)
    return runs


def md_table(head, rows):
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] +
                     ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])


def build(results, out, summary=None):
    runs = collect(results)
    out.mkdir(parents=True, exist_ok=True)
    ok = [r for r in runs if r["bench"]]
    md = ["# Spark model benchmark report", "",
          f"Hardware: NVIDIA GB10 (DGX Spark), 121 GB unified memory. One model at a time, all other model containers stopped. "
          f"Load: concurrency 1/2/4 (max 4 users), 20 requests per level, 256 max new tokens, thinking off where supported. "
          f"Profiles: **gen** (short prompt, 256 generated tokens = pure decode speed) and **rag** (~3k-token document, unique prefix per request so prefix caches cannot hit).", ""]

    if summary and Path(summary).exists():
        md += [Path(summary).read_text(encoding="utf-8"), ""]

    # 1. leaderboard at c=4
    for prof in PROFILES:
        md += [f"## Load results – profile `{prof}`", ""]
        rows = []
        for r in ok:
            for c in sorted(r["bench"].get(prof, {})):
                s = r["bench"][prof][c]
                hw = (r["hw"].get(prof) or {})
                rows.append([r["name"], c, f"{s['success_rate']:.0f}%", f(s["p50_latency"]), f(s["p95_latency"]),
                             f(s["p50_ttft"]), f(s["p95_ttft"]), f(s["mean_gen_tps"], 1), f(s["token_throughput_tps"], 1),
                             f(s["throughput_rps"], 2)])
        md += [md_table(["run", "conc", "ok", "p50 lat s", "p95 lat s", "p50 TTFT s", "p95 TTFT s",
                         "tok/s per req", "agg tok/s", "req/s"], rows), ""]

    # 2. hardware
    md += ["## Machine usage during load (GPU / memory / power)", "",
           md_table(["run", "profile", "GPU util avg/max %", "power avg/max W", "temp max C", "SM MHz avg", "mem used peak GB", "CPU avg %"],
                    [[r["name"], p, f"{f(h['gpu_util'][0], 0)}/{f(h['gpu_util'][1], 0)}",
                      f"{f(h['power_w'][0], 1)}/{f(h['power_w'][1], 1)}", f(h["temp_c"][1], 0), f(h["sm_mhz"][0], 0),
                      f(h["mem_used_mb"][1] / 1024 if h["mem_used_mb"][1] else None, 1), f(h["cpu_pct"][0], 0)]
                     for r in ok for p, h in r["hw"].items() if h]), ""]

    # 3. efficiency
    eff = []
    for r in ok:
        s = r["bench"].get("gen", {}).get(4)
        h = r["hw"].get("gen")
        if s and h and h["power_w"][0]:
            eff.append([r["name"], f(s["token_throughput_tps"], 1), f(h["power_w"][0], 1),
                        f(s["token_throughput_tps"] / h["power_w"][0], 2)])
    eff.sort(key=lambda x: -float(x[3]))
    md += ["## Efficiency (gen profile, concurrency 4)", "", md_table(["run", "agg tok/s", "avg W", "tok/s per W"], eff), ""]

    # 4. tuning deltas
    tun = []
    for r in ok:
        if r["name"].endswith("_baseline"):
            t = next((x for x in ok if x["name"] == r["name"].replace("_baseline", "_tuned")), None)
            if not t:
                continue
            for prof in PROFILES:
                a, b = r["bench"].get(prof, {}).get(4), t["bench"].get(prof, {}).get(4)
                if a and b and a["token_throughput_tps"] and a["p50_ttft"] and b["p50_ttft"]:
                    tun.append([r["name"].replace("_baseline", ""), prof, t["spec"].get("note", ""),
                                f"{a['token_throughput_tps']:.1f} → {b['token_throughput_tps']:.1f}",
                                f"{(b['token_throughput_tps'] / a['token_throughput_tps'] - 1) * 100:+.0f}%",
                                f"{a['p50_ttft']:.2f} → {b['p50_ttft']:.2f}", f"{a['p95_latency']:.2f} → {b['p95_latency']:.2f}"])
    md += ["## Tuning: baseline → tuned (concurrency 4)", "",
           md_table(["model", "profile", "change", "agg tok/s", "Δ", "p50 TTFT s", "p95 lat s"], tun), ""]

    # 5. accuracy
    accs = [r for r in runs if "acc_n" in r]
    md += ["## Accuracy (31 cases: grounded, math, logic, knowledge, instruction, extraction, semantic, multilingual)", "",
           "Rule = deterministic checks. Judge = Qwen3.6-35B (run alone) grading semantic + logical correctness against a reference "
           "(Qwen3.6 judging itself may be slightly lenient).", ""]
    cats = sorted({c for r in accs for c in r["cats"]})
    md += [md_table(["run", "rule", "judge"] + cats + ["mean s/answer"],
                    [[r["name"], f"{r['acc_rule']}/{r['acc_n']} ({r['acc_rule'] / r['acc_n']:.0%})",
                      f"{r['acc_judge'][0]}/{r['acc_judge'][1]}" if r["acc_judge"] else "–"] +
                     [f"{r['cats'][c][1]}/{r['cats'][c][0]}" if c in r["cats"] else "–" for c in cats] + [f(r["acc_lat"])]
                     for r in sorted(accs, key=lambda r: -r["acc_rule"])]), ""]

    fails = [r["name"] for r in runs if r["failed"]]
    if fails:
        md += ["## Runs that failed to start", "", *[f"- {n} (see `{n}/FAILED.log`)" for n in fails], ""]
    md += ["## Common accuracy failures", ""]
    cnt = {}
    for r in accs:
        for x in r["acc_fail"]:
            cnt.setdefault(x["id"], []).append(r["name"])
    md += [f"- `{k}` failed on {len(v)}/{len(accs)} runs" for k, v in sorted(cnt.items(), key=lambda kv: -len(kv[1]))[:10]]
    text = "\n".join(md) + "\n"
    (out / "REPORT.md").write_text(text, encoding="utf-8")
    (out / "REPORT.html").write_text(to_html(text), encoding="utf-8")
    print(f"wrote {out}/REPORT.md and REPORT.html ({len(ok)} benchmarked runs, {len(accs)} accuracy runs)")


def to_html(md):
    """Tiny markdown (headings, tables, bullets, paragraphs) -> html; no deps."""
    import re
    out, rows = [], []

    def inline(t):
        t = html.escape(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        return re.sub(r"`(.+?)`", r"<code>\1</code>", t)

    def flush():
        if rows:
            head, body = rows[0], rows[2:]
            out.append("<div class=t><table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>" +
                       "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table></div>")
            rows.clear()

    for line in md.splitlines():
        if line.startswith("|"):
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
            continue
        flush()
        if line.startswith("# "):
            out.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("### "):
            out.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("- ") or re.match(r"\d+\. ", line):
            out.append(f"<li>{inline(re.sub(r'^(- |\d+\. )', '', line))}</li>")
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
    flush()
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Spark model benchmark report</title><style>body{font:14px system-ui;max-width:1200px;margin:0 auto;padding:24px 16px;"
            "background:#fff;color:#111}h1{font-size:26px}h2{margin-top:36px;border-bottom:1px solid #ddd;padding-bottom:4px}"
            ".t{overflow-x:auto}table{border-collapse:collapse;font-size:12.5px;margin:8px 0}th,td{border:1px solid #ddd;padding:4px 8px;"
            "text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left}th{background:#f3f4f6}"
            "tr:nth-child(even) td{background:#fafafa}code{background:#f3f4f6;padding:1px 4px;border-radius:3px}"
            "@media(prefers-color-scheme:dark){body{background:#111;color:#eee}th{background:#222}td,th{border-color:#333}"
            "tr:nth-child(even) td{background:#181818}code{background:#222}}</style>" + "\n".join(out))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--out", default=str(HERE / "outputs" / "spark_report"))
    ap.add_argument("--summary", help="markdown file inserted before the tables")
    a = ap.parse_args()
    build(Path(a.results), Path(a.out), a.summary)
