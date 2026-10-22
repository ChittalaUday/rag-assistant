#!/usr/bin/env python3
"""
Separate report for the llama.cpp round (Qwen3.6-35B-A3B on llama.cpp, with and without MTP, vs vLLM NVFP4).
  python benchmark_tools/build_llamacpp_report.py <results_dir> [--out FILE]
Default out: benchmark_tools/outputs/spark_report/REPORT_LLAMACPP.html. Runs that have not been done yet are listed as pending.
"""

import argparse
import html
import json
from pathlib import Path

import build_report as br

HERE = Path(__file__).parent
E = html.escape
REF = "vllm_qwen36_baseline"
PLAN = [  # (run id, what it is)
    ("llamacpp_q4kxl_nomtp", "unsloth UD-Q4_K_XL (22.9 GB), MTP off"),
    ("llamacpp_q4kxl_mtp", "unsloth UD-Q4_K_XL, MTP draft on (--spec-type draft-mtp, 3 draft tokens)"),
    ("llamacpp_mxfp4_nomtp", "unsloth MXFP4_MOE (22.2 GB), MTP off"),
    ("llamacpp_mxfp4_mtp", "unsloth MXFP4_MOE, MTP draft on"),
    ("llamacpp_nvfp4_nomtp", "community NVFP4 GGUF 'HQ' (20.5 GB, michaelw9999), MTP off"),
    ("llamacpp_nvfp4_mtp", "community NVFP4 GGUF 'HQ', MTP draft on"),
]


def g(r, p, c, k):
    return (r["bench"].get(p, {}).get(c) or {}).get(k)


def metrics(r, det):
    hw = (r["hw"].get("gen") or {})
    return {
        "per1": g(r, "gen", 1, "mean_gen_tps"), "agg1": g(r, "gen", 1, "token_throughput_tps"),
        "agg2": g(r, "gen", 2, "token_throughput_tps"), "agg4": g(r, "gen", 4, "token_throughput_tps"),
        "t1": g(r, "rag", 1, "p50_ttft"), "t4": g(r, "rag", 4, "p50_ttft"), "lat4": g(r, "rag", 4, "p95_latency"),
        "acc": r["acc_rule"] / r["acc_n"] * 100 if "acc_n" in r else None, "rule": f"{r['acc_rule']}/{r['acc_n']}" if "acc_n" in r else "–",
        "judge": f"{r['acc_judge'][0]}/{r['acc_judge'][1]}" if r.get("acc_judge") else "pending",
        "power": (hw.get("power_w") or [None])[0], "mem": ((hw.get("mem_used_mb") or [None, None])[1] or 0) / 1024 or None,
        "det": det,
    }


def f(v, nd=0, suf=""):
    return "–" if v is None else f"{v:.{nd}f}{suf}"


def bar(v, mx, cls):
    return f'<span class="track"><span class="bar {cls}" style="width:{(v or 0) / mx * 100:.1f}%"></span></span>' if mx else ""


def build(results, out):
    runs = {r["name"]: r for r in br.collect(results) if r["bench"]}
    ref = runs[REF]
    rows = []
    for rid, desc in [(REF, "vLLM, NVIDIA NVFP4 checkpoint (reference, the current winner)")] + PLAN:
        r = runs.get(rid)
        if r is None:
            rows.append((rid, desc, None))
            continue
        dp = results / rid / "determinism.json"
        rows.append((rid, desc, metrics(r, json.loads(dp.read_text()) if dp.exists() else None)))
    done = [(rid, d, m) for rid, d, m in rows if m]
    mx_agg = max(m["agg4"] for _, _, m in done) * 1.02
    mx_t = max(m["t4"] for _, _, m in done) * 1.02
    refm = rows[0][2]

    def tr(rid, desc, m):
        cls = "vllm" if rid == REF else "llama"
        if m is None:
            return f'<tr class="pend"><td class="l"><b>{E(rid)}</b><small>{E(desc)}</small></td><td colspan="8" class="pendtxt">pending — not run yet</td></tr>'
        d = m["det"]
        det = "–" if d is None else (f'<span class="up">identical</span> in a row ({d["sequential_n"]}/{d["sequential_n"]}); '
                                    f'{d["concurrent_distinct"]} different answers in {d["concurrent_n"]} at once'
                                    if d["sequential_distinct"] == 1 else f'<span class="dn">{d["sequential_distinct"]} different answers in a row</span>')
        rel = "" if rid == REF else f'<small class="{"up" if m["agg4"] >= refm["agg4"] else "dn"}">{m["agg4"] / refm["agg4"] * 100:.0f}% of vLLM</small>'
        return (f'<tr><td class="l"><b>{E(rid)}</b><small>{E(desc)}</small></td><td>{f(m["per1"])}</td>'
                f'<td><div class="cell"><span>{f(m["agg4"])}</span>{bar(m["agg4"], mx_agg, cls)}</div>{rel}</td>'
                f'<td><div class="cell"><span>{f(m["t4"], 2, " s")}</span>{bar(m["t4"], mx_t, cls)}</div><small>{f(m["t1"], 2, " s")} alone</small></td>'
                f'<td>{f(m["lat4"], 1, " s")}</td><td>{m["rule"]}<small>judge {m["judge"]}</small></td><td>{f(m["mem"], 0, " GB")}</td>'
                f'<td>{f(m["power"], 0, " W")}</td><td class="det">{det}</td></tr>')

    take = []
    for rid, desc, m in done:
        if rid == REF:
            continue
        take.append(f"<li><b>{E(rid)}</b>: {m['agg4'] / refm['agg4'] * 100:.0f}% of vLLM's total speed with 4 users ({f(m['agg4'])} vs {f(refm['agg4'])} tok/s) and "
                    f"{m['per1'] / refm['per1'] * 100:.0f}% alone ({f(m['per1'])} vs {f(refm['per1'])}); the first word of a long-document answer takes "
                    f"{m['t4'] / refm['t4']:.1f}× as long with 4 users ({f(m['t4'], 2)} s vs {f(refm['t4'], 2)} s); it uses {f(refm['mem'] - m['mem'], 0)} GB "
                    f"less memory ({f(m['mem'])} vs {f(refm['mem'])} GB); {m['rule']} rule-check answers correct (vLLM: {refm['rule']}).</li>")
    pending = [rid for rid, _, m in rows if m is None]
    status = (f"<b>Partial report.</b> {len(done) - 1} of {len(PLAN)} llama.cpp runs are finished; {len(pending)} are pending ("
              f"{', '.join(E(p) for p in pending)}). The Spark was needed for other work, so the benchmark was stopped after the first run; "
              f"the remaining model files keep downloading and the runs resume when the machine is free. AI-judge scores are pending for the same reason "
              f"(the judge is the vLLM Qwen3.6 server).") if pending else "<b>Complete.</b> All llama.cpp runs finished."
    page = f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>llama.cpp vs vLLM — Qwen3.6-35B-A3B</title><style>
:root{{--bg:#f6f6f3;--surface:#fcfcfb;--ink:#0d0d0c;--ink2:#4d4c48;--mut:#8a8984;--line:#e3e2dc;--vllm:#2a78d6;--llama:#1baf7a;--good:#0a7f3f;--bad:#c13b3b;--hot:#fff4e0;color-scheme:light}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0c0c0b;--surface:#171716;--ink:#f1f0ec;--ink2:#c3c2b8;--mut:#8f8e86;--line:#2b2b28;--vllm:#3987e5;--llama:#199e70;--good:#46b86d;--bad:#e66767;--hot:#2a2418;color-scheme:dark}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}}
main{{max-width:1240px;margin:0 auto;padding:28px 18px 60px}}h1{{font-size:28px;letter-spacing:-.02em;margin:0 0 4px}}h2{{font-size:18px;margin:0 0 6px}}
.sub{{color:var(--ink2);max-width:80ch;margin:0 0 16px}}section{{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin:16px 0}}
.banner{{background:var(--hot);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin:14px 0}}
.tw{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;min-width:1000px;font-size:13px}}th{{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);text-align:right;padding:8px 6px;border-bottom:1px solid var(--line)}}
td{{padding:8px 6px;border-bottom:1px solid var(--line);text-align:right;vertical-align:top;font-variant-numeric:tabular-nums}}th.l,td.l{{text-align:left}}td small,td.l small{{display:block;color:var(--mut);font-size:11px;font-weight:400}}
.cell{{display:flex;align-items:center;gap:8px;justify-content:flex-end}}.track{{width:70px;height:7px;border-radius:4px;background:color-mix(in srgb,var(--mut) 18%,transparent);overflow:hidden;flex:none}}
.bar{{display:block;height:100%}}.bar.vllm{{background:var(--vllm)}}.bar.llama{{background:var(--llama)}}.up{{color:var(--good)}}.dn{{color:var(--bad)}}
tr.pend td{{color:var(--mut)}}.pendtxt{{text-align:left;font-style:italic}}td.det{{text-align:left;min-width:230px;font-size:12px}}ul{{margin:6px 0 0 18px;padding:0}}li{{margin:5px 0}}
.how{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}}.how div{{border:1px solid var(--line);border-radius:10px;padding:10px 12px}}.how b{{display:block;margin-bottom:2px}}
code{{background:color-mix(in srgb,var(--mut) 14%,transparent);padding:1px 5px;border-radius:4px;font-size:12px}}a{{color:var(--vllm)}}
</style></head><body><main>
<h1>llama.cpp vs vLLM — Qwen3.6-35B-A3B</h1>
<p class=sub>Does llama.cpp (with NVFP4 / MXFP4 / Q4 weights and MTP speculative decoding) beat vLLM on the NVIDIA GB10 for the model we use? Same tests as the rest of the benchmark: 1, 2 and 4 users, a short-prompt decode test and a ~3,000-token document test, 31 accuracy questions, one model at a time on an otherwise empty machine.</p>
<div class=banner>{status}</div>
<section><h2>Results</h2><div class=tw><table><thead><tr><th class=l>Run</th><th>Speed alone<br>tok/s</th><th>Total, 4 users<br>tok/s</th><th>First token, long doc<br>4 users</th><th>Answer p95<br>long doc, 4 users</th><th>Accuracy<br>rule checks</th><th>Peak memory</th><th>Power</th><th class=l>Reproducible?</th></tr></thead><tbody>
{''.join(tr(*r) for r in rows)}
</tbody></table></div></section>
<section><h2>What the finished runs show</h2><ul>{''.join(take) or '<li>No llama.cpp run has finished yet.</li>'}</ul>
<p class=sub style="margin-top:10px">Why prefill is slower: llama-server's own log shows prompt processing at about 700–1,400 tokens/s per slot, so a 3,000-token document takes seconds to read before the first word; vLLM batches and caches this far better. Why memory is lower: vLLM reserves a fixed share of memory up front (55 GB), while llama.cpp only takes the model and its slots.</p></section>
<section><h2>Is the output reproducible?</h2><p class=sub>The same request was sent 6 times in a row, then 8 times at once (temperature 0, fixed seed). In a row the answers were identical. Several requests at once returned different answers even though sampling is deterministic: with parallel requests the GPU math is batched differently depending on what else is running, which changes tiny floating-point values and can change the words. This is normal for batched serving, not a defect of one engine; MTP speculative decoding (pending) is checked the same way because llama.cpp has a known bug where its state could leak between requests (issue #26425).</p></section>
<section><h2>How it was run</h2><div class=how>
<div><b>Server</b>llama-server, image <code>ghcr.io/ggml-org/llama.cpp:server-cuda13</code> (build 11541), <code>-ngl 99 -fa on -c 65536 -np 4 --reasoning off</code> = 4 parallel slots of 16k tokens.</div>
<div><b>MTP</b><code>--spec-type draft-mtp --spec-draft-n-max 3</code>: the model's multi-token-prediction heads draft three tokens at a time, the main model verifies them.</div>
<div><b>Model files</b>unsloth/Qwen3.6-35B-A3B-MTP-GGUF (UD-Q4_K_XL, MXFP4_MOE) and michaelw9999/Qwen3.6-35B-A3B-NVFP4-MTP-GGUF (a community NVFP4 build, not NVIDIA's own checkpoint).</div>
<div><b>Reference</b>vLLM serving NVIDIA's <code>Qwen3.6-35B-A3B-NVFP4</code> checkpoint with the production flags (fp8 KV cache, flashinfer, 0.40 memory share).</div>
<div><b>Load</b>20 requests per level, 256 new tokens, thinking off; the long-document prompt carries a unique prefix so no cache can help.</div>
<div><b>Data</b><code>benchmark_tools/outputs/spark_run_raw/llamacpp_*</code>, the interactive view is the "llama.cpp vs vLLM" section of <a href="DASHBOARD.html">DASHBOARD.html</a>.</div></div></section>
</main></body></html>"""
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out} ({len(done) - 1}/{len(PLAN)} llama.cpp runs)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--out", default=str(HERE / "outputs" / "spark_report" / "REPORT_LLAMACPP.html"))
    a = ap.parse_args()
    build(Path(a.results), Path(a.out))
