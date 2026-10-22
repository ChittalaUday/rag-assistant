#!/usr/bin/env python3
"""Does the same request always give the same answer? (temperature 0, fixed seed)
Used for llama.cpp + MTP, where speculative state could leak between requests (llama.cpp issue #26425).
  python determinism_test.py --url http://localhost:8101 --model NAME --out determinism.json
Runs 6 identical requests one after another, then 2 rounds of 4 identical requests at once, and
reports how many different answers came back."""

import argparse
import asyncio
import json

import httpx

PROMPT = ("Explain in about 120 words how a B-tree index speeds up a SQL range query, "
          "and name one situation where it does not help.")


async def ask(client, url, model):
    r = await client.post(f"{url}/v1/chat/completions", json={
        "model": model, "messages": [{"role": "user", "content": PROMPT}], "temperature": 0, "seed": 7,
        "max_tokens": 200, "chat_template_kwargs": {"enable_thinking": False}}, timeout=300)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    async with httpx.AsyncClient() as c:
        seq = [await ask(c, a.url, a.model) for _ in range(6)]
        par = []
        for _ in range(2):
            par += await asyncio.gather(*(ask(c, a.url, a.model) for _ in range(4)))
    allv = seq + par
    out = {"sequential_distinct": len(set(seq)), "sequential_n": len(seq),
           "concurrent_distinct": len(set(par)), "concurrent_n": len(par),
           "all_distinct": len(set(allv)), "all_n": len(allv),
           "identical_to_first": sum(x == seq[0] for x in allv),
           "sample": seq[0][:300], "variants": [v[:300] for v in dict.fromkeys(allv)][:4]}
    json.dump(out, open(a.out, "w"), indent=2, ensure_ascii=False)
    print(json.dumps({k: v for k, v in out.items() if k not in ("sample", "variants")}))


if __name__ == "__main__":
    asyncio.run(main())
