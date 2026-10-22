#!/usr/bin/env python3
"""
Test & Benchmark Ollama Models
------------------------------
Runs performance benchmarks for local Ollama models:
- Warmup run per model
- Live progress and real-time streaming TTFT measurement
- Prompt tokens/sec and generation tokens/sec
- Automatically generates HTML/JSON/CSV reports afterwards in benchmark_tools/outputs/test_models/
"""

import sys
from benchmark_ollama import main

if __name__ == "__main__":
    # If no CLI arguments provided, default to benchmarking the 3 target models
    if len(sys.argv) == 1:
        sys.argv.extend([
            "--url", "http://172.16.16.248:11434",
            "--models", "qwen3.5:9b,lfm2:24b,nemotron-cascade-2:30b",
            "--levels", "1",
            "--users", "1",
            "--requests", "3",
            "--warmup", "1",
        ])
    main()