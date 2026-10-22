#!/usr/bin/env python3
"""
vLLM (OpenAI-compatible) Load & Latency Benchmark
-------------------------------------------------
Reuses the Ollama benchmarker's runner, stats and HTML/JSON/CSV reports; only the
wire protocol differs (POST /v1/chat/completions with SSE streaming).
Token speeds come from `usage` + client timings (vLLM has no server-side durations):
  prompt_tps     = prompt_tokens / TTFT
  generation_tps = completion_tokens / (latency - TTFT)

Reports go to benchmark_tools/outputs/benchmark_vllm/.
  python benchmark_tools/benchmark_vllm.py --levels 1,5,10 --users 10 --requests 3
"""

import asyncio
import json
import sys
import time
from typing import Any, Dict, List, Optional

import httpx

import benchmark_ollama as bo
from benchmark_ollama import OllamaBenchmarker, RequestResult, console

DEFAULT_VLLM_URL = "http://172.16.16.248:8008"
DEFAULT_VLLM_MODEL = "nvidia/Qwen3.6-35B-A3B-NVFP4"


class VllmBenchmarker(OllamaBenchmarker):
    async def verify_connection(self) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}/v1/models")
            resp.raise_for_status()
            return {"connected": True, "models": [m["id"] for m in resp.json().get("data", [])]}

    def _build_payload(self, model: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": self._prompt_text()}],
            "stream": self.stream,
            "max_tokens": self.num_predict,
            "temperature": self.temperature,
        }
        if self.stream:
            payload["stream_options"] = {"include_usage": True}
        if self.think is not None:  # Qwen3-style chat-template switch
            payload["chat_template_kwargs"] = {"enable_thinking": self.think}
        return payload

    async def _send_single_request(
        self, client: httpx.AsyncClient, model: str, scenario_id: str,
        concurrency: int, user_id: int, req_idx: int,
    ) -> RequestResult:
        start = time.perf_counter()
        ttft: Optional[float] = None
        pieces: List[str] = []
        usage: Dict[str, Any] = {}
        status = 0
        try:
            async with client.stream(
                "POST", f"{self.base_url}/v1/chat/completions",
                json=self._build_payload(model), timeout=self.timeout,
            ) as resp:
                status = resp.status_code
                if status != 200:
                    raise RuntimeError(f"HTTP {status}: {(await resp.aread()).decode('utf-8', 'replace')[:200]}")
                if self.stream:
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        chunk = json.loads(data)
                        if chunk.get("error"):
                            raise RuntimeError(f"vLLM error: {chunk['error']}")
                        usage = chunk.get("usage") or usage
                        for choice in chunk.get("choices", []):
                            d = choice.get("delta", {})
                            content = d.get("content") or ""
                            # reasoning field name varies by vLLM version/parser
                            thinking = d.get("reasoning_content") or d.get("reasoning") or ""
                            if (content or thinking) and ttft is None:
                                ttft = time.perf_counter() - start
                            if content:
                                pieces.append(content)
                else:
                    body = json.loads(await resp.aread())
                    usage = body.get("usage") or {}
                    pieces.append(body["choices"][0]["message"].get("content") or "")
                    ttft = time.perf_counter() - start

            latency = time.perf_counter() - start
            p_cnt, g_cnt = usage.get("prompt_tokens"), usage.get("completion_tokens")
            decode = latency - ttft if ttft is not None else None
            return RequestResult(
                scenario_id=scenario_id, model=model, concurrency=concurrency,
                user_id=user_id, request_index=req_idx, ok=True, status_code=status or 200,
                latency=latency, ttft=ttft, server_total=latency,
                prompt_tokens=p_cnt, output_tokens=g_cnt,
                prompt_seconds=ttft, generation_seconds=decode,
                prompt_tps=(p_cnt / ttft) if p_cnt and ttft else None,
                generation_tps=(g_cnt / decode) if g_cnt and decode and decode > 0 else None,
                answer="".join(pieces),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return RequestResult(
                scenario_id=scenario_id, model=model, concurrency=concurrency,
                user_id=user_id, request_index=req_idx, ok=False, status_code=status,
                latency=time.perf_counter() - start, error=str(exc) or type(exc).__name__,
            )

    def export_html(self, output_path: str):
        html = bo.generate_html_report(self).replace("Ollama", "vLLM")
        bo.Path(output_path).write_text(html, encoding="utf-8")
        console.print(f"[bold green]✓ Interactive HTML report generated:[/bold green] [cyan]{output_path}[/cyan]")


async def main_async():
    # Parse with the Ollama CLI, swapping defaults unless the user set them.
    for flag, default in (("--url", DEFAULT_VLLM_URL), ("--models", DEFAULT_VLLM_MODEL)):
        if flag not in sys.argv:
            sys.argv += [flag, default]
    args = bo.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    think = {"true": True, "false": False}.get(args.think)
    prompt = args.prompt
    if len(prompt) < 255 and bo.Path(prompt).is_file():
        prompt = bo.Path(prompt).read_text(encoding="utf-8")

    bench = VllmBenchmarker(
        base_url=args.url, models=models, concurrency_levels=levels, users=args.users,
        requests_per_user=args.requests, warmup=args.warmup, timeout=args.timeout,
        num_ctx=args.ctx, num_predict=args.max_tokens, temperature=args.temperature,
        think=think, prompt=prompt, stream=not args.no_stream, simultaneous=args.simultaneous,
    )
    bench.unique_prompts = args.unique_prompts
    try:
        served = (await bench.verify_connection())["models"]
    except Exception as exc:
        console.print(f"[bold red]❌ Failed to connect to vLLM at {args.url}: {exc}[/bold red]")
        sys.exit(1)
    console.print(f"[green]✓ Connected to vLLM ({', '.join(served)})[/green]")
    missing = [m for m in models if m not in served]
    if missing:
        console.print(f"[yellow]⚠️  Not served by vLLM: {', '.join(missing)}[/yellow]")

    await bench.run()
    bench.export_html(bo.output_path(args.output_html, "html"))
    bench.export_json(bo.output_path(args.output_json, "json"))
    bench.export_csv(bo.output_path(args.output_csv, "csv"))


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        console.print("\n[yellow]Benchmark terminated.[/yellow]")
