#!/usr/bin/env python3
"""
Accuracy test for local LLM endpoints (Ollama or vLLM/OpenAI-compatible).
Each case in accuracy_cases.json is graded by rules (any/all/regex/none/json) and,
with --judge-url/--judge-model, by an LLM judge that checks the answer against the
reference semantically and logically. Reports -> benchmark_tools/outputs/accuracy_test/.

  python benchmark_tools/accuracy_test.py --backend vllm   --url http://HOST:8008 --model nvidia/Qwen3.6-35B-A3B-NVFP4
  python benchmark_tools/accuracy_test.py --backend ollama --url http://HOST:11434 --model qwen3.5:9b
  # judge afterwards, with one judge model running alone:
  python benchmark_tools/accuracy_test.py --judge-only outputs/accuracy_test/<file>.json \
      --judge-url http://HOST:8008 --judge-model nvidia/Qwen3.6-35B-A3B-NVFP4
"""

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

import httpx

HERE = Path(__file__).parent
OUT = HERE / "outputs" / "accuracy_test"
THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def chat(backend, url, model, prompt, max_tokens, think, timeout=300):
    """Return (answer, latency_s, error)."""
    msgs = [{"role": "user", "content": prompt}]
    t = time.perf_counter()
    try:
        if backend == "ollama":
            body = {"model": model, "messages": msgs, "stream": False, "keep_alive": "10m",
                    "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": 8192}}
            if think is not None:
                body["think"] = think
            r = httpx.post(f"{url}/api/chat", json=body, timeout=timeout)
            r.raise_for_status()
            text = r.json()["message"].get("content", "")
        else:
            body = {"model": model, "messages": msgs, "temperature": 0, "max_tokens": max_tokens}
            if think is not None:
                body["chat_template_kwargs"] = {"enable_thinking": think}
            r = httpx.post(f"{url}/v1/chat/completions", json=body, timeout=timeout)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"].get("content") or ""
        return THINK_RE.sub("", text).strip(), time.perf_counter() - t, ""
    except Exception as exc:
        return "", time.perf_counter() - t, str(exc) or type(exc).__name__


def rule_check(case, ans):
    """-> (passed, reasons). All configured rules must hold."""
    low, why = ans.lower(), []
    if "any" in case and not any(k.lower() in low for k in case["any"]):
        why.append(f"none of {case['any']}")
    for k in case.get("all", []):
        if k.lower() not in low:
            why.append(f"missing '{k}'")
    if "regex" in case and not all(re.search(p, ans, re.I | re.M) for p in case["regex"]):
        why.append(f"regex {case['regex']} not matched")
    for p in case.get("none", []):
        if re.search(p, ans, re.I | re.M):
            why.append(f"forbidden /{p}/ matched")
    if "json" in case:
        try:
            got = json.loads(ans)
            if any(got.get(k) != v for k, v in case["json"].items()):
                why.append(f"json mismatch: {got}")
        except Exception:
            why.append("not valid JSON")
    return not why, why


JUDGE_PROMPT = """You are a strict grader. Decide whether the candidate answer is semantically AND logically correct
for the question, judged against the reference. Wording may differ; the facts, numbers and reasoning conclusion must match.
Penalise invented facts. Reply with ONLY a JSON object: {{"correct": true|false, "reason": "<short>"}}

Question:
{q}

Reference answer / criteria:
{ref}

Candidate answer:
{a}"""


def judge(args, case, ans):
    if not ans:
        return False, "empty answer"
    p = JUDGE_PROMPT.format(q=case["prompt"], ref=case.get("judge") or case["ref"], a=ans)
    out, _, err = chat(args.judge_backend, args.judge_url, args.judge_model, p, 400, False)
    m = re.search(r"\{.*\}", out, re.S)
    try:
        j = json.loads(m.group(0))
        return bool(j["correct"]), str(j.get("reason", ""))
    except Exception:
        return False, f"judge unparseable: {err or out[:80]}"


def summarize(rows):
    cats = {}
    for r in rows:
        c = cats.setdefault(r["cat"], [0, 0, 0, 0])  # n, rule_pass, judge_pass, judged
        c[0] += 1
        c[1] += r["rule_pass"]
        if r.get("judge_pass") is not None:
            c[3] += 1
            c[2] += r["judge_pass"]
    return cats


def write_report(path, meta, rows):
    path.write_text(json.dumps({"meta": meta, "results": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    cats = summarize(rows)
    n = len(rows)
    rp = sum(r["rule_pass"] for r in rows)
    judged = [r for r in rows if r.get("judge_pass") is not None]
    lines = [f"# Accuracy: {meta['model']} ({meta['backend']})", "",
             f"- Rule accuracy: **{rp}/{n} = {rp / n:.0%}**"]
    if judged:
        jp = sum(r["judge_pass"] for r in judged)
        lines.append(f"- Judge accuracy ({meta.get('judge_model')}): **{jp}/{len(judged)} = {jp / len(judged):.0%}**")
    lines += [f"- Mean latency: {sum(r['latency'] for r in rows) / n:.2f}s", "",
              "| Category | n | rule | judge |", "|---|---|---|---|"]
    for c, (cn, cr, cj, cd) in cats.items():
        lines.append(f"| {c} | {cn} | {cr}/{cn} | {f'{cj}/{cd}' if cd else '-'} |")
    lines += ["", "## Failures", ""]
    for r in rows:
        bad = not r["rule_pass"] or r.get("judge_pass") is False
        if bad:
            lines.append(f"- **{r['id']}** ({r['cat']}): answer `{r['answer'][:160]!r}` | rule: {r['rule_why']} | judge: {r.get('judge_reason')}")
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:3 + (1 if judged else 0)]), f"\nReport: {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["ollama", "vllm"], default="vllm")
    ap.add_argument("--url", default="http://172.16.16.248:8008")
    ap.add_argument("--model", default="nvidia/Qwen3.6-35B-A3B-NVFP4")
    ap.add_argument("--think", choices=["true", "false", "default"], default="false")
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--cases", default=str(HERE / "accuracy_cases.json"))
    ap.add_argument("--judge-url")
    ap.add_argument("--judge-model")
    ap.add_argument("--judge-backend", choices=["ollama", "vllm"], default="vllm")
    ap.add_argument("--judge-only", help="existing report .json to (re)judge, no generation")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    cases = {c["id"]: c for c in json.loads(Path(args.cases).read_text(encoding="utf-8"))}

    if args.judge_only:
        path = Path(args.judge_only)
        data = json.loads(path.read_text(encoding="utf-8"))
        meta, rows = data["meta"], data["results"]
    else:
        think = {"true": True, "false": False}.get(args.think)
        meta = {"backend": args.backend, "url": args.url, "model": args.model, "think": args.think,
                "max_tokens": args.max_tokens, "date": datetime.now().isoformat()}
        rows = []
        for c in cases.values():
            ans, lat, err = chat(args.backend, args.url, args.model, c["prompt"], args.max_tokens, think)
            ok, why = rule_check(c, ans) if not err else (False, [f"error: {err}"])
            rows.append({"id": c["id"], "cat": c["cat"], "answer": ans, "latency": lat,
                         "rule_pass": ok, "rule_why": why})
            print(f"{'PASS' if ok else 'FAIL'} {c['id']:<18} {lat:5.1f}s  {ans[:60]!r}")
        slug = re.sub(r"[^\w.-]+", "_", args.model)
        path = OUT / f"{slug}_{datetime.now():%Y%m%d_%H%M%S}.json"

    if args.judge_url and args.judge_model:
        meta["judge_model"] = args.judge_model
        for r in rows:
            r["judge_pass"], r["judge_reason"] = judge(args, cases[r["id"]], r["answer"])
    write_report(path, meta, rows)


if __name__ == "__main__":
    main()
