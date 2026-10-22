#!/usr/bin/env python3
"""
Ollama Load & Latency Benchmarking Suite
----------------------------------------
Measures real-world Ollama model performance under concurrent load:
- Time to First Token (TTFT) via streaming
- Latency percentiles (P50, P75, P90, P95, P99, Min, Max)
- Prompt & generation token throughput (tokens/sec)
- Success rates, errors, server durations, and RPS
- Generates rich interactive HTML, JSON, and CSV reports
"""

import argparse
import asyncio
import csv
import html
import json
import math
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

console = Console()

OUTPUTS_DIR = Path(__file__).parent / "outputs"


def output_path(explicit: Optional[str], ext: str) -> str:
    """Explicit path wins; else outputs/<entry-script-name>/<script>_report_<timestamp>.<ext>."""
    if explicit:
        return explicit
    stem = Path(sys.argv[0]).stem
    out_dir = OUTPUTS_DIR / stem
    out_dir.mkdir(parents=True, exist_ok=True)
    return str(out_dir / f"{stem}_report_{datetime.now():%Y%m%d_%H%M%S}.{ext}")

DEFAULT_OLLAMA_URL = "http://172.16.16.248:11434"
DEFAULT_MODELS = ["qwen3.5:9b", "lfm2:24b", "nemotron-cascade-2:30b"]
DEFAULT_CONCURRENCY = [1, 5, 10]
DEFAULT_USERS = 10
DEFAULT_REQUESTS = 3
DEFAULT_WARMUP = 1
DEFAULT_TIMEOUT = 180.0
DEFAULT_CTX = 4096
DEFAULT_MAX_TOKENS = 150
DEFAULT_TEMPERATURE = 0.0

DEFAULT_PROMPT = """Answer using only the provided document.

Document:
The weather monitoring system processes historical rainfall,
temperature, humidity, and wind observations.
It stores the processed observations in PostgreSQL.
Its API retrieves observations based on location and date.

Question:
Which database does the system use, and what data
can be retrieved from it?

Answer concisely."""


@dataclass
class RequestResult:
    scenario_id: str
    model: str
    concurrency: int
    user_id: int
    request_index: int
    ok: bool
    status_code: int
    latency: float  # seconds
    ttft: Optional[float] = None  # seconds
    load_duration: Optional[float] = None  # seconds
    server_total: Optional[float] = None  # seconds
    prompt_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    prompt_seconds: Optional[float] = None
    generation_seconds: Optional[float] = None
    prompt_tps: Optional[float] = None
    generation_tps: Optional[float] = None
    answer: str = ""
    error: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ScenarioSummary:
    scenario_id: str
    model: str
    concurrency: int
    users: int
    requests_per_user: int
    total_planned: int
    completed: int
    successful: int
    failed: int
    success_rate: float
    elapsed_seconds: float
    throughput_rps: float
    token_throughput_tps: float
    # Latency percentiles
    min_latency: Optional[float] = None
    mean_latency: Optional[float] = None
    std_latency: Optional[float] = None
    p50_latency: Optional[float] = None
    p75_latency: Optional[float] = None
    p90_latency: Optional[float] = None
    p95_latency: Optional[float] = None
    p99_latency: Optional[float] = None
    max_latency: Optional[float] = None
    # TTFT percentiles
    p50_ttft: Optional[float] = None
    p95_ttft: Optional[float] = None
    mean_ttft: Optional[float] = None
    # Token speeds
    mean_gen_tps: Optional[float] = None
    p50_gen_tps: Optional[float] = None
    p95_gen_tps: Optional[float] = None
    mean_prompt_tps: Optional[float] = None
    mean_load_duration: Optional[float] = None
    sample_answer: str = ""


def ns_to_sec(ns: Optional[int]) -> Optional[float]:
    if ns is None:
        return None
    return float(ns) / 1e9


def percentile(data: List[float], p: float) -> Optional[float]:
    if not data:
        return None
    s = sorted(data)
    k = (len(s) - 1) * (p / 100.0)
    floor_k = math.floor(k)
    ceil_k = math.ceil(k)
    if floor_k == ceil_k:
        return s[int(k)]
    d0 = s[floor_k] * (ceil_k - k)
    d1 = s[ceil_k] * (k - floor_k)
    return d0 + d1


def compute_summary(
    scenario_id: str,
    model: str,
    concurrency: int,
    users: int,
    requests_per_user: int,
    results: List[RequestResult],
    elapsed: float,
) -> ScenarioSummary:
    planned = users * requests_per_user
    completed = len(results)
    ok_results = [r for r in results if r.ok]
    successful = len(ok_results)
    failed = completed - successful
    success_rate = (successful / completed * 100.0) if completed > 0 else 0.0

    latencies = [r.latency for r in ok_results]
    ttfts = [r.ttft for r in ok_results if r.ttft is not None]
    gen_tpss = [r.generation_tps for r in ok_results if r.generation_tps is not None]
    prompt_tpss = [r.prompt_tps for r in ok_results if r.prompt_tps is not None]
    loads = [r.load_duration for r in ok_results if r.load_duration is not None]

    mean_lat = (sum(latencies) / len(latencies)) if latencies else None
    std_lat = (
        math.sqrt(sum((x - mean_lat) ** 2 for x in latencies) / (len(latencies) - 1))
        if len(latencies) > 1 and mean_lat is not None
        else 0.0 if latencies else None
    )

    total_tokens = sum(r.output_tokens or 0 for r in ok_results)
    rps = (completed / elapsed) if elapsed > 0 else 0.0
    token_tps = (total_tokens / elapsed) if elapsed > 0 else 0.0

    sample_answer = ""
    for r in reversed(ok_results):
        if r.answer:
            sample_answer = r.answer
            break

    return ScenarioSummary(
        scenario_id=scenario_id,
        model=model,
        concurrency=concurrency,
        users=users,
        requests_per_user=requests_per_user,
        total_planned=planned,
        completed=completed,
        successful=successful,
        failed=failed,
        success_rate=success_rate,
        elapsed_seconds=elapsed,
        throughput_rps=rps,
        token_throughput_tps=token_tps,
        min_latency=min(latencies) if latencies else None,
        mean_latency=mean_lat,
        std_latency=std_lat,
        p50_latency=percentile(latencies, 50),
        p75_latency=percentile(latencies, 75),
        p90_latency=percentile(latencies, 90),
        p95_latency=percentile(latencies, 95),
        p99_latency=percentile(latencies, 99),
        max_latency=max(latencies) if latencies else None,
        p50_ttft=percentile(ttfts, 50),
        p95_ttft=percentile(ttfts, 95),
        mean_ttft=(sum(ttfts) / len(ttfts)) if ttfts else None,
        mean_gen_tps=(sum(gen_tpss) / len(gen_tpss)) if gen_tpss else None,
        p50_gen_tps=percentile(gen_tpss, 50),
        p95_gen_tps=percentile(gen_tpss, 95),
        mean_prompt_tps=(sum(prompt_tpss) / len(prompt_tpss)) if prompt_tpss else None,
        mean_load_duration=(sum(loads) / len(loads)) if loads else None,
        sample_answer=sample_answer,
    )


class OllamaBenchmarker:
    def __init__(
        self,
        base_url: str,
        models: List[str],
        concurrency_levels: List[int],
        users: int,
        requests_per_user: int,
        warmup: int,
        timeout: float,
        num_ctx: int,
        num_predict: int,
        temperature: float,
        think: Optional[bool],
        prompt: str,
        stream: bool = True,
        simultaneous: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.models = models
        self.concurrency_levels = concurrency_levels
        self.users = users
        self.requests_per_user = requests_per_user
        self.warmup = warmup
        self.timeout = timeout
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.temperature = temperature
        self.think = think
        self.prompt = prompt
        self.stream = stream
        self.simultaneous = simultaneous

        self.all_results: List[RequestResult] = []
        self.summaries: List[ScenarioSummary] = []
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        self.stop_requested = False

    async def verify_connection(self) -> Dict[str, Any]:
        """Verify reachability and retrieve available models."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            available = [m["name"] for m in data.get("models", [])]
            return {"connected": True, "models": available}

    def _prompt_text(self) -> str:
        """Unique per-request prefix defeats prefix/prompt caching so long-prompt TTFT is honest."""
        if getattr(self, "unique_prompts", False):
            return f"[request {uuid.uuid4().hex}]\n{self.prompt}"
        return self.prompt

    def _build_payload(self, model: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": self._prompt_text()}],
            "stream": self.stream,
            "keep_alive": "10m",
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "num_predict": self.num_predict,
            },
        }
        if self.think is not None:
            payload["think"] = self.think
        return payload

    async def _send_single_request(
        self,
        client: httpx.AsyncClient,
        model: str,
        scenario_id: str,
        concurrency: int,
        user_id: int,
        req_idx: int,
    ) -> RequestResult:
        payload = self._build_payload(model)
        start_t = time.perf_counter()
        ttft: Optional[float] = None
        pieces: List[str] = []
        final_chunk: Dict[str, Any] = {}
        status_code = 0

        try:
            if self.stream:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=self.timeout,
                ) as resp:
                    status_code = resp.status_code
                    if resp.status_code != 200:
                        err_text = await resp.aread()
                        raise RuntimeError(
                            f"HTTP {resp.status_code}: {err_text.decode('utf-8', 'replace')[:200]}"
                        )
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        chunk = json.loads(line)
                        if chunk.get("error"):
                            raise RuntimeError(f"Ollama error: {chunk['error']}")
                        msg = chunk.get("message", {})
                        content = msg.get("content", "")
                        thinking = msg.get("thinking", "")
                        if (content or thinking) and ttft is None:
                            ttft = time.perf_counter() - start_t
                        if content:
                            pieces.append(content)
                        if chunk.get("done"):
                            final_chunk = chunk
            else:
                resp = await client.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=self.timeout,
                )
                status_code = resp.status_code
                resp.raise_for_status()
                final_chunk = resp.json()
                pieces.append(final_chunk.get("message", {}).get("content", ""))
                ttft = time.perf_counter() - start_t

            latency = time.perf_counter() - start_t
            p_eval_cnt = final_chunk.get("prompt_eval_count")
            p_eval_dur = ns_to_sec(final_chunk.get("prompt_eval_duration"))
            g_eval_cnt = final_chunk.get("eval_count")
            g_eval_dur = ns_to_sec(final_chunk.get("eval_duration"))

            prompt_tps = (
                (p_eval_cnt / p_eval_dur) if (p_eval_cnt and p_eval_dur and p_eval_dur > 0) else None
            )
            gen_tps = (
                (g_eval_cnt / g_eval_dur) if (g_eval_cnt and g_eval_dur and g_eval_dur > 0) else None
            )

            return RequestResult(
                scenario_id=scenario_id,
                model=model,
                concurrency=concurrency,
                user_id=user_id,
                request_index=req_idx,
                ok=True,
                status_code=status_code or 200,
                latency=latency,
                ttft=ttft,
                load_duration=ns_to_sec(final_chunk.get("load_duration")),
                server_total=ns_to_sec(final_chunk.get("total_duration")),
                prompt_tokens=p_eval_cnt,
                output_tokens=g_eval_cnt,
                prompt_seconds=p_eval_dur,
                generation_seconds=g_eval_dur,
                prompt_tps=prompt_tps,
                generation_tps=gen_tps,
                answer="".join(pieces),
                error="",
            )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            latency = time.perf_counter() - start_t
            return RequestResult(
                scenario_id=scenario_id,
                model=model,
                concurrency=concurrency,
                user_id=user_id,
                request_index=req_idx,
                ok=False,
                status_code=status_code,
                latency=latency,
                ttft=None,
                error=str(exc) or type(exc).__name__,
            )

    async def _run_warmup(
        self, client: httpx.AsyncClient, model: str, progress: Progress, task_id: Any
    ):
        for i in range(self.warmup):
            if self.stop_requested:
                break
            progress.update(
                task_id,
                description=f"[yellow]Warming up [bold]{model}[/bold] ({i+1}/{self.warmup})...",
            )
            res = await self._send_single_request(
                client=client,
                model=model,
                scenario_id="warmup",
                concurrency=1,
                user_id=0,
                req_idx=i + 1,
            )
            if not res.ok:
                console.print(
                    f"[yellow]⚠️  Warmup request {i+1} for {model} warning: {res.error}[/yellow]"
                )

    async def _run_scenario(
        self,
        model: str,
        concurrency: int,
        progress: Progress,
        total_task_id: Any,
        scenario_task_id: Any,
        skip_warmup: bool = False,
    ) -> List[RequestResult]:
        scenario_id = f"{model}|c{concurrency}"
        total_reqs = self.users * self.requests_per_user
        progress.reset(scenario_task_id, total=total_reqs)
        progress.update(
            scenario_task_id,
            description=f"[cyan]Benchmarking [bold]{model}[/bold] @ {concurrency} workers",
            visible=True,
        )

        scenario_results: List[RequestResult] = []
        limits = httpx.Limits(max_connections=concurrency + 20, max_keepalive_connections=concurrency)
        semaphore = asyncio.Semaphore(concurrency)

        start_time = time.perf_counter()

        async with httpx.AsyncClient(limits=limits, timeout=self.timeout + 10.0) as client:
            # First warmup
            if self.warmup > 0 and not skip_warmup:
                await self._run_warmup(client, model, progress, scenario_task_id)

            jobs: List[tuple[int, int]] = []
            for u in range(1, self.users + 1):
                for r in range(1, self.requests_per_user + 1):
                    jobs.append((u, r))

            progress.update(
                scenario_task_id,
                description=f"[green]Running [bold]{model}[/bold] (Concurrency: {concurrency})",
            )

            async def worker(u_id: int, req_id: int):
                if self.stop_requested:
                    return
                async with semaphore:
                    if self.stop_requested:
                        return
                    res = await self._send_single_request(
                        client=client,
                        model=model,
                        scenario_id=scenario_id,
                        concurrency=concurrency,
                        user_id=u_id,
                        req_idx=req_id,
                    )
                    scenario_results.append(res)
                    self.all_results.append(res)
                    progress.advance(scenario_task_id, 1)
                    progress.advance(total_task_id, 1)

                    # Update live status on the scenario bar
                    ok_count = sum(1 for x in scenario_results if x.ok)
                    last_lat = f"{res.latency:.2f}s"
                    last_tps = f"{res.generation_tps:.1f}t/s" if res.generation_tps else "—"
                    status_str = f"[green]{model}[/green] @ c={concurrency} | Ok: {ok_count}/{len(scenario_results)} | Last: {last_lat} ({last_tps})"
                    progress.update(scenario_task_id, description=status_str)

            tasks = [asyncio.create_task(worker(u, r)) for (u, r) in jobs]
            await asyncio.gather(*tasks, return_exceptions=True)

        elapsed = time.perf_counter() - start_time
        summary = compute_summary(
            scenario_id=scenario_id,
            model=model,
            concurrency=concurrency,
            users=self.users,
            requests_per_user=self.requests_per_user,
            results=scenario_results,
            elapsed=elapsed,
        )
        self.summaries.append(summary)
        progress.update(
            scenario_task_id,
            description=f"[bold green]✓ Completed [white]{model}[/white] @ c={concurrency} in {elapsed:.1f}s",
        )
        return scenario_results

    async def run(self):
        self.start_time = datetime.now()
        total_scenarios = len(self.models) * len(self.concurrency_levels)
        total_requests = total_scenarios * self.users * self.requests_per_user

        mode_str = "[bold magenta]Simultaneous (All models running concurrently)[/bold magenta]" if self.simultaneous else "Sequential (Model by model)"

        console.print(
            Panel.fit(
                f"[bold cyan]Ollama Load & Latency Benchmark[/bold cyan]\n"
                f"[dim]Endpoint:[/dim] [white]{self.base_url}[/white]\n"
                f"[dim]Models:[/dim] [green]{', '.join(self.models)}[/green]\n"
                f"[dim]Execution Mode:[/dim] {mode_str}\n"
                f"[dim]Concurrency levels:[/dim] [yellow]{', '.join(map(str, self.concurrency_levels))}[/yellow]\n"
                f"[dim]Virtual Users:[/dim] [white]{self.users}[/white] | "
                f"[dim]Requests/User:[/dim] [white]{self.requests_per_user}[/white] | "
                f"[dim]Total Planned:[/dim] [bold yellow]{total_requests}[/bold yellow] requests\n"
                f"[dim]Warmup:[/dim] {self.warmup} | [dim]Thinking Mode:[/dim] {self.think} | [dim]Streaming:[/dim] {self.stream}",
                border_style="cyan",
            )
        )

        # Pre-warm models if simultaneous mode and warmup requested
        if self.simultaneous and self.warmup > 0:
            console.print("[dim]Pre-warming models before simultaneous benchmark...[/dim]")
            async with httpx.AsyncClient(timeout=self.timeout + 15.0) as warmup_client:
                for model in self.models:
                    console.print(f"  [dim]• Priming [bold]{model}[/bold] ({self.warmup} req)...[/dim]")
                    for w in range(self.warmup):
                        res = await self._send_single_request(
                            client=warmup_client,
                            model=model,
                            scenario_id="warmup",
                            concurrency=1,
                            user_id=0,
                            req_idx=w + 1,
                        )
                        if res.ok:
                            gen_tps_str = f"{res.generation_tps:.1f} t/s" if res.generation_tps else "—"
                            console.print(f"    [green]✓ {model} ready (lat: {res.latency:.2f}s, speed: {gen_tps_str})[/green]")
                        else:
                            console.print(f"    [yellow]⚠️ {model} warmup warning: {res.error}[/yellow]")

        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(bar_width=35),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        ) as progress:
            total_task = progress.add_task(
                "[bold blue]Overall Benchmark Progress", total=total_requests
            )

            try:
                if self.simultaneous:
                    for conc in self.concurrency_levels:
                        if self.stop_requested:
                            break
                        # Dedicated progress bar per model
                        model_tasks = {
                            m: progress.add_task(
                                f"[cyan]Initializing [bold]{m}[/bold] @ c={conc}...",
                                total=self.users * self.requests_per_user,
                                visible=True,
                            )
                            for m in self.models
                        }
                        tasks = [
                            self._run_scenario(
                                model=m,
                                concurrency=conc,
                                progress=progress,
                                total_task_id=total_task,
                                scenario_task_id=model_tasks[m],
                                skip_warmup=True,
                            )
                            for m in self.models
                        ]
                        await asyncio.gather(*tasks, return_exceptions=True)
                else:
                    scenario_task = progress.add_task("[cyan]Starting...", total=10, visible=False)
                    for model in self.models:
                        if self.stop_requested:
                            break
                        for conc in self.concurrency_levels:
                            if self.stop_requested:
                                break
                            await self._run_scenario(
                                model=model,
                                concurrency=conc,
                                progress=progress,
                                total_task_id=total_task,
                                scenario_task_id=scenario_task,
                            )
            except KeyboardInterrupt:
                console.print(
                    "\n[bold red]⚠️  Benchmark interrupted by user. Generating reports with completed data...[/bold red]"
                )
                self.stop_requested = True
            finally:
                self.end_time = datetime.now()

        # Sort summaries consistently by concurrency and model order
        model_order = {m: i for i, m in enumerate(self.models)}
        self.summaries.sort(key=lambda s: (s.concurrency, model_order.get(s.model, 999)))

        self._print_terminal_summary()

    def _print_terminal_summary(self):
        console.print("\n")
        table = Table(
            title="📊 Ollama Benchmark Summary Results",
            title_style="bold magenta",
            header_style="bold cyan",
            border_style="dim",
        )
        table.add_column("Model", style="white", no_wrap=True)
        table.add_column("Conc", justify="right")
        table.add_column("Reqs", justify="right")
        table.add_column("Success %", justify="right")
        table.add_column("P50 Lat", justify="right")
        table.add_column("P95 Lat", justify="right")
        table.add_column("P99 Lat", justify="right")
        table.add_column("P95 TTFT", justify="right")
        table.add_column("Gen Tok/s", justify="right", style="green")
        table.add_column("RPS", justify="right", style="cyan")
        table.add_column("Duration", justify="right", style="dim")

        for s in self.summaries:
            succ_color = "green" if s.success_rate >= 99 else "yellow" if s.success_rate >= 80 else "red"
            table.add_row(
                s.model,
                str(s.concurrency),
                f"{s.successful}/{s.completed}",
                f"[{succ_color}]{s.success_rate:.1f}%[/{succ_color}]",
                f"{s.p50_latency:.2f}s" if s.p50_latency is not None else "—",
                f"{s.p95_latency:.2f}s" if s.p95_latency is not None else "—",
                f"{s.p99_latency:.2f}s" if s.p99_latency is not None else "—",
                f"{s.p95_ttft:.2f}s" if s.p95_ttft is not None else "—",
                f"{s.mean_gen_tps:.1f}" if s.mean_gen_tps is not None else "—",
                f"{s.throughput_rps:.2f}",
                f"{s.elapsed_seconds:.1f}s",
            )

        console.print(table)
        total_ok = sum(s.successful for s in self.summaries)
        total_req = sum(s.completed for s in self.summaries)
        total_dur = (
            (self.end_time - self.start_time).total_seconds()
            if (self.start_time and self.end_time)
            else 0.0
        )
        console.print(
            f"[dim]Total run time:[/dim] [bold]{total_dur:.1f}s[/bold] | "
            f"[dim]Total completed requests:[/dim] [bold]{total_ok}/{total_req}[/bold]"
        )

    def export_json(self, output_path: str):
        data = {
            "metadata": {
                "benchmark_started": self.start_time.isoformat() if self.start_time else None,
                "benchmark_finished": self.end_time.isoformat() if self.end_time else None,
                "base_url": self.base_url,
                "models": self.models,
                "simultaneous": self.simultaneous,
                "concurrency_levels": self.concurrency_levels,
                "users": self.users,
                "requests_per_user": self.requests_per_user,
                "warmup": self.warmup,
                "timeout": self.timeout,
                "ctx": self.num_ctx,
                "max_tokens": self.num_predict,
                "temperature": self.temperature,
                "think": self.think,
                "prompt": self.prompt,
            },
            "summaries": [asdict(s) for s in self.summaries],
            "requests": [asdict(r) for r in self.all_results],
        }
        Path(output_path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        console.print(f"[dim]JSON report saved to:[/dim] [cyan]{output_path}[/cyan]")

    def export_csv(self, output_path: str):
        if not self.all_results:
            return
        fields = [
            "timestamp",
            "scenario_id",
            "model",
            "concurrency",
            "user_id",
            "request_index",
            "ok",
            "status_code",
            "latency",
            "ttft",
            "prompt_tokens",
            "output_tokens",
            "prompt_seconds",
            "generation_seconds",
            "prompt_tps",
            "generation_tps",
            "load_duration",
            "server_total",
            "error",
            "answer",
        ]
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for r in self.all_results:
                writer.writerow(asdict(r))
        console.print(f"[dim]CSV report saved to:[/dim] [cyan]{output_path}[/cyan]")

    def export_html(self, output_path: str):
        html_content = generate_html_report(self)
        Path(output_path).write_text(html_content, encoding="utf-8")
        console.print(f"[bold green]✓ Interactive HTML report generated:[/bold green] [cyan]{output_path}[/cyan]")


def generate_html_report(bench: OllamaBenchmarker) -> str:
    """Generate a modern, beautiful, zero-dependency interactive HTML report."""
    summaries_data = [asdict(s) for s in bench.summaries]
    requests_data = [asdict(r) for r in bench.all_results]
    config_data = {
        "base_url": bench.base_url,
        "models": bench.models,
        "simultaneous": bench.simultaneous,
        "concurrency_levels": bench.concurrency_levels,
        "users": bench.users,
        "requests_per_user": bench.requests_per_user,
        "warmup": bench.warmup,
        "timeout": bench.timeout,
        "num_ctx": bench.num_ctx,
        "num_predict": bench.num_predict,
        "temperature": bench.temperature,
        "think": bench.think,
        "prompt": bench.prompt,
        "started": bench.start_time.isoformat() if bench.start_time else "",
        "finished": bench.end_time.isoformat() if bench.end_time else "",
    }

    total_reqs = len(requests_data)
    total_ok = sum(1 for r in requests_data if r["ok"])
    overall_success = (total_ok / total_reqs * 100) if total_reqs else 0.0
    all_lats = [r["latency"] for r in requests_data if r["ok"]]
    overall_p95 = percentile(all_lats, 95) if all_lats else 0.0
    peak_gen_tps = max([r["generation_tps"] or 0 for r in requests_data], default=0.0)

    # Pre-render SVG charts
    # 1. Latency Chart (P50, P95, P99 by Scenario)
    svg_latency_chart = ""
    if summaries_data:
        max_p99 = max([s.get("p99_latency") or 0.1 for s in summaries_data], default=1.0) * 1.15
        chart_w = 980
        chart_h = max(260, len(summaries_data) * 52 + 70)
        left = 260
        right = 40
        bar_area_w = chart_w - left - right

        lines = [f'<svg viewBox="0 0 {chart_w} {chart_h}" class="chart-svg">']
        # Grid lines
        for i in range(5):
            x = left + bar_area_w * (i / 4)
            val = max_p99 * (i / 4)
            lines.append(f'<line x1="{x}" y1="35" x2="{x}" y2="{chart_h - 25}" stroke="#26354a" stroke-dasharray="3"/>')
            lines.append(f'<text x="{x}" y="24" fill="#7d93b3" font-size="11" text-anchor="middle">{val:.2f}s</text>')

        for i, s in enumerate(summaries_data):
            y = 45 + i * 52
            label = f"{s['model']} @ c={s['concurrency']}"
            if len(label) > 34:
                label = label[:33] + "…"
            lines.append(f'<text x="15" y="{y + 18}" fill="#e1e8f5" font-size="12" font-weight="600">{html.escape(label)}</text>')

            p99 = s.get("p99_latency") or 0
            p95 = s.get("p95_latency") or 0
            p50 = s.get("p50_latency") or 0

            w99 = max(0, bar_area_w * (p99 / max_p99))
            w95 = max(0, bar_area_w * (p95 / max_p99))
            w50 = max(0, bar_area_w * (p50 / max_p99))

            lines.append(f'<rect x="{left}" y="{y}" width="{w99}" height="7" rx="3" fill="#4d6f9e"/>')
            lines.append(f'<rect x="{left}" y="{y + 9}" width="{w95}" height="7" rx="3" fill="#4fa5ff"/>')
            lines.append(f'<rect x="{left}" y="{y + 18}" width="{w50}" height="7" rx="3" fill="#4ae3a7"/>')
            lines.append(f'<text x="{left + w95 + 8}" y="{y + 15}" fill="#a5b8d2" font-size="10">P95: {p95:.2f}s</text>')

        lines.append(f'<text x="{left}" y="{chart_h - 6}" fill="#8aa2c2" font-size="11">■ P50: Mint (#4ae3a7)   ■ P95: Blue (#4fa5ff)   ■ P99: Slate (#4d6f9e)</text>')
        lines.append('</svg>')
        svg_latency_chart = "".join(lines)
    else:
        svg_latency_chart = '<div class="empty">No scenario data available</div>'

    # Pre-render Summary Table Rows
    summary_rows_html = []
    for s in summaries_data:
        rate = s["success_rate"]
        badge_cls = "badge-green" if rate >= 99 else "badge-yellow" if rate >= 80 else "badge-red"
        p50 = f"{s['p50_latency']:.2f}s" if s.get("p50_latency") is not None else "—"
        p95 = f"{s['p95_latency']:.2f}s" if s.get("p95_latency") is not None else "—"
        p99 = f"{s['p99_latency']:.2f}s" if s.get("p99_latency") is not None else "—"
        ttft95 = f"{s['p95_ttft']:.2f}s" if s.get("p95_ttft") is not None else "—"
        gen_tps = f"{s['mean_gen_tps']:.1f}" if s.get("mean_gen_tps") is not None else "—"
        prompt_tps = f"{s['mean_prompt_tps']:.1f}" if s.get("mean_prompt_tps") is not None else "—"
        rps = f"{s['throughput_rps']:.2f}"
        summary_rows_html.append(f"""
        <tr>
            <td class="bold">{html.escape(s['model'])}</td>
            <td class="center"><span class="pill-concurrency">c={s['concurrency']}</span></td>
            <td class="center">{s['successful']} / {s['completed']}</td>
            <td class="center"><span class="{badge_cls}">{rate:.1f}%</span></td>
            <td>{p50}</td>
            <td class="bold-highlight">{p95}</td>
            <td>{p99}</td>
            <td>{ttft95}</td>
            <td class="green-text bold">{gen_tps}</td>
            <td>{prompt_tps}</td>
            <td class="cyan-text">{rps}</td>
            <td class="dim">{s['elapsed_seconds']:.1f}s</td>
        </tr>
        """)

    # Pre-render Sample Answers
    sample_answers_html = []
    seen_models = set()
    for s in summaries_data:
        if s["model"] not in seen_models and s.get("sample_answer"):
            seen_models.add(s["model"])
            ans = html.escape(s["sample_answer"].strip())
            sample_answers_html.append(f"""
            <div class="answer-card">
                <div class="answer-header">
                    <span class="model-badge">{html.escape(s['model'])}</span>
                    <span class="dim">Tokens: ~{s.get('mean_gen_tps', 0):.0f} tok/s</span>
                </div>
                <pre class="answer-body">{ans}</pre>
            </div>
            """)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ollama Benchmark Report</title>
<style>
:root {{
    --bg: #090e17;
    --card: #111a29;
    --card-border: #223249;
    --text: #e6eefb;
    --text-muted: #8ca2c4;
    --accent: #5397ff;
    --green: #4be3a7;
    --yellow: #f8c057;
    --red: #ff7676;
    --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    font-family: var(--font);
    background: var(--bg);
    color: var(--text);
    padding: 32px 24px 80px;
    line-height: 1.5;
}}
.container {{ max-width: 1380px; margin: 0 auto; }}
header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 20px;
    margin-bottom: 28px;
    padding-bottom: 20px;
    border-bottom: 1px solid var(--card-border);
    flex-wrap: wrap;
}}
.eyebrow {{
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: var(--accent);
    font-weight: 700;
}}
h1 {{ font-size: 28px; font-weight: 800; letter-spacing: -0.5px; margin: 4px 0 6px; }}
.subtitle {{ color: var(--text-muted); font-size: 13px; }}
.header-actions {{ display: flex; gap: 10px; align-items: center; }}
.btn {{
    background: #1c2b42;
    color: #eaf1fc;
    border: 1px solid #334768;
    padding: 8px 14px;
    border-radius: 9px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
    text-decoration: none;
    display: inline-flex;
    align-items: center;
    gap: 6px;
}}
.btn:hover {{ background: #283c5c; border-color: #4b6794; }}
.kpis {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
    margin-bottom: 28px;
}}
.kpi {{
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: 14px;
    padding: 20px;
}}
.kpi-label {{ font-size: 12px; color: var(--text-muted); font-weight: 600; }}
.kpi-val {{ font-size: 28px; font-weight: 800; margin: 8px 0 2px; letter-spacing: -0.5px; }}
.kpi-unit {{ font-size: 11px; color: var(--text-muted); }}

.panel {{
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: 16px;
    padding: 24px;
    margin-bottom: 28px;
}}
.panel h2 {{ font-size: 17px; font-weight: 700; margin-bottom: 4px; }}
.panel-desc {{ font-size: 12px; color: var(--text-muted); margin-bottom: 18px; }}

.chart-svg {{ width: 100%; height: auto; display: block; background: #0c1422; border-radius: 10px; border: 1px solid #1c283a; padding: 10px; }}

.tabs {{ display: flex; gap: 8px; margin-bottom: 18px; border-bottom: 1px solid var(--card-border); padding-bottom: 10px; }}
.tab-btn {{
    background: transparent;
    border: none;
    color: var(--text-muted);
    font-size: 13px;
    font-weight: 600;
    padding: 8px 16px;
    border-radius: 8px;
    cursor: pointer;
}}
.tab-btn.active {{
    background: #1c2d47;
    color: #fff;
    border: 1px solid #37537e;
}}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}

.table-wrap {{ overflow-x: auto; max-height: 600px; border-radius: 10px; border: 1px solid var(--card-border); }}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    text-align: left;
    white-space: nowrap;
}}
th {{
    background: #172439;
    color: #a4bad9;
    font-weight: 700;
    padding: 11px 14px;
    position: sticky;
    top: 0;
    z-index: 10;
    border-bottom: 1px solid #27374e;
}}
td {{
    padding: 10px 14px;
    border-bottom: 1px solid #1a273b;
}}
tr:hover td {{ background: #162438; }}
.bold {{ font-weight: 700; color: #fff; }}
.bold-highlight {{ font-weight: 700; color: #72aeff; }}
.green-text {{ color: var(--green); }}
.cyan-text {{ color: #5ad4f7; }}
.dim {{ color: var(--text-muted); font-size: 11px; }}
.center {{ text-align: center; }}

.badge-green {{ background: #15382b; color: #58efa9; padding: 3px 8px; border-radius: 6px; font-weight: 700; font-size: 11px; }}
.badge-yellow {{ background: #3d3118; color: #ffd166; padding: 3px 8px; border-radius: 6px; font-weight: 700; font-size: 11px; }}
.badge-red {{ background: #3d1c22; color: #ff858d; padding: 3px 8px; border-radius: 6px; font-weight: 700; font-size: 11px; }}
.pill-concurrency {{ background: #1a283f; border: 1px solid #2f4363; padding: 3px 8px; border-radius: 12px; font-size: 11px; color: #bed2ee; }}

.filter-bar {{ display: flex; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; align-items: center; }}
.input-filter {{
    background: #0b1320;
    border: 1px solid #283952;
    color: #eaf1fc;
    padding: 8px 12px;
    border-radius: 8px;
    font-size: 12px;
    outline: none;
    min-width: 200px;
}}
.input-filter:focus {{ border-color: var(--accent); }}

.answer-card {{
    background: #0d1524;
    border: 1px solid var(--card-border);
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 14px;
}}
.answer-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
.model-badge {{ background: #1d3252; color: #7cb2ff; padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 700; }}
.answer-body {{ font-family: ui-monospace, Consolas, monospace; font-size: 12px; color: #c0d3ee; white-space: pre-wrap; }}

pre.code-box {{ background: #0b121e; padding: 14px; border-radius: 10px; font-size: 11px; border: 1px solid #1f2e44; overflow: auto; }}

@media (max-width: 900px) {{
    .kpis {{ grid-template-columns: 1fr 1fr; }}
}}
@media (max-width: 500px) {{
    .kpis {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<div class="container">
    <header>
        <div>
            <div class="eyebrow">Load Test & Latency Profiling Report{" &bull; SIMULTANEOUS PARALLEL RUN" if bench.simultaneous else ""}</div>
            <h1>Ollama Benchmark Results</h1>
            <div class="subtitle">
                Generated {datetime.now().strftime('%b %d, %Y at %H:%M:%S')} &bull; Host: <code>{html.escape(bench.base_url)}</code> &bull; Mode: <strong>{"Simultaneous (Parallel)" if bench.simultaneous else "Sequential"}</strong>
            </div>
        </div>
        <div class="header-actions">
            <button class="btn" onclick="downloadJSON()">↓ Download JSON</button>
            <button class="btn" onclick="downloadCSV()">↓ Download CSV</button>
            <button class="btn" onclick="window.print()">🖨 Print / PDF</button>
        </div>
    </header>

    <div class="kpis">
        <div class="kpi">
            <div class="kpi-label">TOTAL MEASURED REQUESTS</div>
            <div class="kpi-val">{total_reqs}</div>
            <div class="kpi-unit">{len(bench.models)} models &bull; {len(bench.concurrency_levels)} concurrency levels</div>
        </div>
        <div class="kpi">
            <div class="kpi-label">OVERALL SUCCESS RATE</div>
            <div class="kpi-val" style="color: {'var(--green)' if overall_success >= 99 else 'var(--yellow)'};">{overall_success:.1f}%</div>
            <div class="kpi-unit">{total_ok} successful &bull; {total_reqs - total_ok} failed</div>
        </div>
        <div class="kpi">
            <div class="kpi-label">OVERALL P95 LATENCY</div>
            <div class="kpi-val" style="color: var(--accent);">{overall_p95:.2f}s</div>
            <div class="kpi-unit">Across all completed runs</div>
        </div>
        <div class="kpi">
            <div class="kpi-label">PEAK GENERATION SPEED</div>
            <div class="kpi-val" style="color: var(--green);">{peak_gen_tps:.1f}</div>
            <div class="kpi-unit">Tokens / second</div>
        </div>
    </div>

    <section class="panel">
        <h2>📈 Latency Percentiles (P50 / P95 / P99)</h2>
        <div class="panel-desc">Comparison of response times across tested models and concurrency settings. Measured from client dispatch to final stream chunk.</div>
        {svg_latency_chart}
    </section>

    <section class="panel">
        <div class="tabs">
            <button class="tab-btn active" onclick="switchTab('summary')">Summary Table</button>
            <button class="tab-btn" onclick="switchTab('requests')">All Requests ({len(requests_data)})</button>
            <button class="tab-btn" onclick="switchTab('answers')">Sample Answers</button>
            <button class="tab-btn" onclick="switchTab('config')">Benchmark Config</button>
        </div>

        <!-- TAB: SUMMARY -->
        <div id="tab-summary" class="tab-content active">
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>Model</th>
                            <th class="center">Concurrency</th>
                            <th class="center">Completed</th>
                            <th class="center">Success Rate</th>
                            <th>P50 Latency</th>
                            <th>P95 Latency</th>
                            <th>P99 Latency</th>
                            <th>P95 TTFT</th>
                            <th>Gen Tok/s</th>
                            <th>Prompt Tok/s</th>
                            <th>Throughput</th>
                            <th>Duration</th>
                        </tr>
                    </thead>
                    <tbody>
                        {"".join(summary_rows_html)}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- TAB: REQUESTS -->
        <div id="tab-requests" class="tab-content">
            <div class="filter-bar">
                <input type="text" id="reqSearch" class="input-filter" placeholder="Search model, error, answer..." onkeyup="filterRequests()">
                <select id="reqFilterStatus" class="input-filter" onchange="filterRequests()">
                    <option value="all">All Results</option>
                    <option value="ok">Success Only (OK)</option>
                    <option value="fail">Errors Only</option>
                </select>
            </div>
            <div class="table-wrap">
                <table id="requestsTable">
                    <thead>
                        <tr>
                            <th>Model</th>
                            <th class="center">Conc</th>
                            <th class="center">User</th>
                            <th class="center">Req #</th>
                            <th class="center">Status</th>
                            <th>Latency</th>
                            <th>TTFT</th>
                            <th>Gen Tok/s</th>
                            <th>Prompt Tok/s</th>
                            <th>Tokens Out</th>
                            <th>Error / Message</th>
                        </tr>
                    </thead>
                    <tbody id="requestsTbody"></tbody>
                </table>
            </div>
        </div>

        <!-- TAB: SAMPLE ANSWERS -->
        <div id="tab-answers" class="tab-content">
            <div class="panel-desc">Representative completions produced by each model for the benchmark prompt:</div>
            {"".join(sample_answers_html) if sample_answers_html else '<div class="dim">No sample answers recorded.</div>'}
        </div>

        <!-- TAB: CONFIG -->
        <div id="tab-config" class="tab-content">
            <pre class="code-box">{html.escape(json.dumps(config_data, indent=2))}</pre>
        </div>
    </section>
</div>

<script id="raw-data" type="application/json">
{json.dumps({"config": config_data, "summaries": summaries_data, "requests": requests_data})}
</script>

<script>
const rawData = JSON.parse(document.getElementById('raw-data').textContent);

function switchTab(name) {{
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    event.currentTarget.classList.add('active');
    document.getElementById('tab-' + name).classList.add('active');
    if (name === 'requests' && !window.requestsRendered) {{
        renderRequestsTable();
    }}
}}

function renderRequestsTable() {{
    window.requestsRendered = true;
    const tbody = document.getElementById('requestsTbody');
    const reqs = rawData.requests;
    let out = '';
    for (let i = 0; i < reqs.length; i++) {{
        const r = reqs[i];
        const statusBadge = r.ok ? '<span class="badge-green">OK</span>' : '<span class="badge-red">Error</span>';
        const lat = r.latency ? r.latency.toFixed(2) + 's' : '—';
        const ttft = r.ttft ? r.ttft.toFixed(2) + 's' : '—';
        const gtps = r.generation_tps ? r.generation_tps.toFixed(1) : '—';
        const ptps = r.prompt_tps ? r.prompt_tps.toFixed(1) : '—';
        const tokens = r.output_tokens !== null && r.output_tokens !== undefined ? r.output_tokens : '—';
        const errMsg = r.error ? `<span style="color:#ff858d">${{escapeHtml(r.error)}}</span>` : '<span class="dim">—</span>';
        
        out += `<tr data-ok="${{r.ok}}" data-text="${{escapeHtml((r.model + ' ' + (r.error||'') + ' ' + (r.answer||'')).toLowerCase())}}">
            <td class="bold">${{escapeHtml(r.model)}}</td>
            <td class="center">c=${{r.concurrency}}</td>
            <td class="center">${{r.user_id}}</td>
            <td class="center">${{r.request_index}}</td>
            <td class="center">${{statusBadge}}</td>
            <td class="bold-highlight">${{lat}}</td>
            <td>${{ttft}}</td>
            <td class="green-text">${{gtps}}</td>
            <td>${{ptps}}</td>
            <td>${{tokens}}</td>
            <td>${{errMsg}}</td>
        </tr>`;
    }}
    tbody.innerHTML = out;
}}

function filterRequests() {{
    const query = (document.getElementById('reqSearch').value || '').toLowerCase();
    const status = document.getElementById('reqFilterStatus').value;
    const rows = document.querySelectorAll('#requestsTbody tr');
    rows.forEach(tr => {{
        const isOk = tr.getAttribute('data-ok') === 'true';
        const text = tr.getAttribute('data-text') || '';
        let matchStatus = true;
        if (status === 'ok') matchStatus = isOk;
        if (status === 'fail') matchStatus = !isOk;
        const matchQuery = !query || text.includes(query);
        tr.style.display = (matchStatus && matchQuery) ? '' : 'none';
    }});
}}

function escapeHtml(s) {{
    return String(s || '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}

function downloadJSON() {{
    const blob = new Blob([JSON.stringify(rawData, null, 2)], {{ type: 'application/json' }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'ollama-benchmark-report.json';
    a.click();
    URL.revokeObjectURL(url);
}}

function downloadCSV() {{
    const reqs = rawData.requests;
    if (!reqs.length) return alert('No requests available to download');
    const keys = Object.keys(reqs[0]);
    const lines = [keys.join(',')];
    for (const r of reqs) {{
        lines.push(keys.map(k => '"' + String(r[k] ?? '').replace(/"/g, '""') + '"').join(','));
    }}
    const blob = new Blob([lines.join('\\n')], {{ type: 'text/csv' }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'ollama-benchmark-report.csv';
    a.click();
    URL.revokeObjectURL(url);
}}
</script>
</body>
</html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark Ollama language models for latency, throughput, and concurrent scaling."
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_OLLAMA_URL,
        help=f"Ollama base URL (default: {DEFAULT_OLLAMA_URL})",
    )
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help=f"Comma-separated model names (default: {','.join(DEFAULT_MODELS)})",
    )
    parser.add_argument(
        "--levels",
        default=",".join(map(str, DEFAULT_CONCURRENCY)),
        help=f"Comma-separated concurrency levels to test (default: {','.join(map(str, DEFAULT_CONCURRENCY))})",
    )
    parser.add_argument(
        "--users",
        type=int,
        default=DEFAULT_USERS,
        help=f"Virtual users per scenario (default: {DEFAULT_USERS})",
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=DEFAULT_REQUESTS,
        help=f"Requests per virtual user (default: {DEFAULT_REQUESTS})",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP,
        help=f"Number of warmup requests per model (default: {DEFAULT_WARMUP})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Per-request timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--ctx",
        type=int,
        default=DEFAULT_CTX,
        help=f"Context window tokens num_ctx (default: {DEFAULT_CTX})",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help=f"Max output tokens num_predict (default: {DEFAULT_MAX_TOKENS})",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=DEFAULT_TEMPERATURE,
        help=f"Sampling temperature (default: {DEFAULT_TEMPERATURE})",
    )
    parser.add_argument(
        "--think",
        choices=["true", "false", "omit"],
        default="false",
        help="Reasoning/thinking mode: true, false, or omit (default: false)",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="Prompt text or path to a text file containing the prompt",
    )
    parser.add_argument(
        "--unique-prompts",
        action="store_true",
        help="Prefix every request with a unique tag so prompt/prefix caches cannot hit",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable streaming (TTFT will equal total latency)",
    )
    parser.add_argument(
        "--simultaneous",
        "--parallel-models",
        dest="simultaneous",
        action="store_true",
        help="Run benchmark against all models simultaneously instead of sequentially",
    )
    parser.add_argument(
        "--output-html",
        default=None,
        help="Path for interactive HTML report (default: benchmark_tools/outputs/<script>/)",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Path for structured JSON report (default: benchmark_tools/outputs/<script>/)",
    )
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Path for tabular CSV report (default: benchmark_tools/outputs/<script>/)",
    )
    return parser.parse_args()


async def main_async():
    args = parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    levels = [int(lvl.strip()) for lvl in args.levels.split(",") if lvl.strip()]

    prompt_text = args.prompt
    if len(args.prompt) < 255:
        try:
            if Path(args.prompt).is_file():
                prompt_text = Path(args.prompt).read_text(encoding="utf-8")
        except OSError:
            pass

    think_val: Optional[bool] = None
    if args.think == "true":
        think_val = True
    elif args.think == "false":
        think_val = False

    bench = OllamaBenchmarker(
        base_url=args.url,
        models=models,
        concurrency_levels=levels,
        users=args.users,
        requests_per_user=args.requests,
        warmup=args.warmup,
        timeout=args.timeout,
        num_ctx=args.ctx,
        num_predict=args.max_tokens,
        temperature=args.temperature,
        think=think_val,
        prompt=prompt_text,
        stream=not args.no_stream,
        simultaneous=args.simultaneous,
    )
    bench.unique_prompts = args.unique_prompts

    console.print(f"[dim]Checking connection to {args.url}...[/dim]")
    try:
        status = await bench.verify_connection()
        server_models = status.get("models", [])
        console.print(f"[green]✓ Connected to Ollama server ({len(server_models)} models found)[/green]")
        missing = [m for m in models if m not in server_models]
        if missing:
            console.print(
                f"[yellow]⚠️  Note: Some requested models may not be in Ollama's local tags: {', '.join(missing)}[/yellow]"
            )
    except Exception as exc:
        console.print(f"[bold red]❌ Failed to connect to Ollama at {args.url}: {exc}[/bold red]")
        sys.exit(1)

    await bench.run()

    # Generate reports
    bench.export_html(output_path(args.output_html, "html"))
    bench.export_json(output_path(args.output_json, "json"))
    bench.export_csv(output_path(args.output_csv, "csv"))


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        console.print("\n[yellow]Benchmark terminated.[/yellow]")


if __name__ == "__main__":
    main()
