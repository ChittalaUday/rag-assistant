#!/usr/bin/env python3
"""
Server-side suite runner (run on the GPU box as root): tests ONE model at a time in isolation.
For each spec: stop every other model container -> start target -> wait ready -> sample hardware
(1 Hz) -> accuracy test -> load benchmark (profiles short + rag, concurrency 1/2/4) -> stop target.
Finally judges all accuracy answers with a judge model, then restores the original containers.

  sudo python3 run_suite.py [--only substr,substr] [--results DIR]
Results: DIR/<spec>/{accuracy,bench_*}.*, DIR/<spec>/hw.csv, DIR/phases.csv, DIR/suite.log
"""

import argparse
import csv
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

HOME = Path("/home/uday/bench")
PY = str(HOME / "venv/bin/python")
HF = "/home/uday/.cache/huggingface"
MANAGED = {"vllm-qwen36", "vllm-gpt-oss-20b", "clef-flash"}  # model-sleeper wakes/sleeps these; keep --restart no
ORIGINALS = ["vllm-qwen36", "vllm-gpt-oss-20b", "vllm-gemma-12b", "clef-flash", "ollama"]  # restored at the end
VLLM_PORT, OLLAMA_PORT, LLAMA_PORT = 8100, 11435, 8101
LLAMA_IMAGE = "ghcr.io/ggml-org/llama.cpp:server-cuda13"
LLAMA_DIR = "/home/uday/models/llamacpp"  # GGUF files; mounted read-only into the container
LEVELS, USERS, REQS, MAXTOK = "1,2,4", "4", "5", "256"
PROFILES = {"gen": str(HOME / "prompts/gen.txt"), "rag": str(HOME / "prompts/rag_long.txt")}

QWEN_ENV = {"HF_HUB_OFFLINE": "1", "CUTE_DSL_ARCH": "sm_121a", "FLASHINFER_DISABLE_VERSION_CHECK": "1"}
QWEN_ARGS = ["--trust-remote-code", "--quantization", "modelopt", "--kv-cache-dtype", "fp8",
             "--attention-backend", "flashinfer", "--moe-backend", "marlin", "--max-model-len", "32768",
             "--enable-chunked-prefill", "--enable-prefix-caching", "--reasoning-parser", "qwen3"]
OFF = {"HF_HUB_OFFLINE": "1"}
ON = {"HF_HUB_OFFLINE": "0"}
NEW_ENV = {**QWEN_ENV, "HF_HUB_OFFLINE": "0"}


def vllm(name, model, env, args, note, think="false", ro=True, **kw):
    return dict(name=name, kind="vllm", model=model, env=env, args=args, note=note, think=think, ro=ro, **kw)


def llama(name, alias, repo, file, args, note, think="false"):
    """llama.cpp llama-server (OpenAI-compatible API, same client as vLLM); `alias` is the model id."""
    return dict(name=name, kind="llamacpp", model=alias, repo=repo, file=file, args=args, env={}, note=note,
                think=think, ro=False)


def ollama(name, model, env, note, think="omit"):
    return dict(name=name, kind="ollama", model=model, env=env, note=note, think=think)


OL_BASE = {"OLLAMA_NUM_PARALLEL": "4", "OLLAMA_KEEP_ALIVE": "30m"}  # = your current container config
OL_TUNED = {**OL_BASE, "OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0"}

LL_ARGS = ["-ngl", "99", "-fa", "on", "-c", "65536", "-np", "4", "--jinja", "--reasoning", "off"]  # 4 slots x 16k
LL_MTP = ["--spec-type", "draft-mtp", "--spec-draft-n-max", "3"]  # multi-token-prediction draft heads

SPECS = [
    vllm("vllm_qwen36_baseline", "nvidia/Qwen3.6-35B-A3B-NVFP4", QWEN_ENV,
         QWEN_ARGS + ["--gpu-memory-utilization", "0.40", "--max-num-seqs", "4", "--max-num-batched-tokens", "8192"],
         "current prod flags"),
    vllm("vllm_qwen36_tuned", "nvidia/Qwen3.6-35B-A3B-NVFP4", QWEN_ENV,
         QWEN_ARGS + ["--gpu-memory-utilization", "0.60", "--max-num-seqs", "4", "--max-num-batched-tokens", "16384"],
         "gpu-mem 0.60 (alone on box), batched tokens 16384"),
    vllm("vllm_gptoss20b_baseline", "openai/gpt-oss-20b", OFF,
         ["--gpu-memory-utilization", "0.35", "--max-model-len", "8192", "--max-num-seqs", "4"], "current prod flags",
         think="omit", acc_tok=2048),
    vllm("vllm_gptoss20b_tuned", "openai/gpt-oss-20b", OFF,
         ["--gpu-memory-utilization", "0.50", "--max-model-len", "16384", "--max-num-seqs", "4",
          "--max-num-batched-tokens", "16384", "--enable-prefix-caching"], "gpu-mem 0.50, ctx 16k", think="omit", acc_tok=2048),
    vllm("vllm_gemma3_12b_baseline", "google/gemma-3-12b-it", OFF,
         ["--gpu-memory-utilization", "0.35", "--max-model-len", "8192", "--max-num-seqs", "2"],
         "current prod flags (max-num-seqs 2)", think="omit"),
    vllm("vllm_gemma3_12b_tuned", "google/gemma-3-12b-it", OFF,
         ["--gpu-memory-utilization", "0.50", "--max-model-len", "8192", "--max-num-seqs", "4",
          "--max-num-batched-tokens", "8192", "--enable-prefix-caching"], "max-num-seqs 4, gpu-mem 0.50", think="omit"),
    vllm("vllm_qwen3_4b_new", "Qwen/Qwen3-4B-Instruct-2507", ON,
         ["--gpu-memory-utilization", "0.30", "--max-model-len", "16384", "--max-num-seqs", "4",
          "--enable-prefix-caching"], "NEW small model", think="omit", ro=False),
    ollama("ollama_qwen35_9b_baseline", "qwen3.5:9b", OL_BASE, "current container env", think="false"),
    ollama("ollama_qwen35_9b_tuned", "qwen3.5:9b", OL_TUNED, "flash-attn + q8_0 KV", think="false"),
    ollama("ollama_lfm2_24b_baseline", "lfm2:24b", OL_BASE, "current container env"),
    ollama("ollama_lfm2_24b_tuned", "lfm2:24b", OL_TUNED, "flash-attn + q8_0 KV"),
    ollama("ollama_nemotron_30b_baseline", "nemotron-cascade-2:30b", OL_BASE, "current container env", think="false"),
    ollama("ollama_nemotron_30b_tuned", "nemotron-cascade-2:30b", OL_TUNED, "flash-attn + q8_0 KV", think="false"),
    ollama("ollama_qwen3_0p6b_baseline", "qwen3:0.6b", OL_BASE, "current container env", think="false"),
    ollama("ollama_llama32_3b_new", "llama3.2:3b", OL_TUNED, "NEW small model"),
    ollama("ollama_gemma3_4b_new", "gemma3:4b", OL_TUNED, "NEW small model"),
    # --- extra small / Spark-optimised / quantised models (NVFP4 = Blackwell native 4-bit) ---
    vllm("vllm_qwen3_8b_nvfp4", "nvidia/Qwen3-8B-NVFP4", NEW_ENV,
         ["--gpu-memory-utilization", "0.30", "--max-model-len", "16384", "--max-num-seqs", "4",
          "--enable-prefix-caching"], "NEW NVIDIA NVFP4 8B", ro=False),
    vllm("vllm_llama31_8b_nvfp4", "nvidia/Llama-3.1-8B-Instruct-NVFP4", NEW_ENV,
         ["--gpu-memory-utilization", "0.30", "--max-model-len", "16384", "--max-num-seqs", "4",
          "--enable-prefix-caching"], "NEW NVIDIA NVFP4 8B", think="omit", ro=False),
    vllm("vllm_qwen3_30b_a3b_nvfp4", "nvidia/Qwen3-30B-A3B-NVFP4", NEW_ENV,
         ["--gpu-memory-utilization", "0.40", "--max-model-len", "16384", "--max-num-seqs", "4",
          "--enable-prefix-caching"], "NEW NVIDIA NVFP4 MoE 30B-A3B", ro=False),
    vllm("vllm_gemma3_12b_w4a16", "RedHatAI/gemma-3-12b-it-quantized.w4a16", ON,
         ["--gpu-memory-utilization", "0.35", "--max-model-len", "8192", "--max-num-seqs", "4",
          "--enable-prefix-caching"], "NEW 4-bit quantised gemma 12B", think="omit", ro=False),
    ollama("ollama_gptoss_20b_mxfp4", "gpt-oss:20b", OL_TUNED, "NEW MXFP4 (native 4-bit)", think="omit"),
    ollama("ollama_qwen3_4b_new", "qwen3:4b", OL_TUNED, "NEW small model", think="false"),
    ollama("ollama_phi4_mini_new", "phi4-mini:latest", OL_TUNED, "NEW small model"),
    ollama("ollama_granite33_2b_new", "granite3.3:2b", OL_TUNED, "NEW small model"),
    ollama("ollama_ministral3_3b_new", "ministral-3:3b", OL_TUNED, "NEW small model"),
    # --- round 2: more NVFP4 + FP8 models (found via the Hugging Face API) ---
    vllm("vllm_qwen36_27b_nvfp4", "nvidia/Qwen3.6-27B-NVFP4", NEW_ENV,
         QWEN_ARGS + ["--gpu-memory-utilization", "0.40", "--max-num-seqs", "4", "--max-num-batched-tokens", "8192"],
         "R2 NVFP4 dense 27B", ro=False),
    vllm("vllm_qwen3_14b_nvfp4", "nvidia/Qwen3-14B-NVFP4", NEW_ENV,
         ["--gpu-memory-utilization", "0.30", "--max-model-len", "16384", "--max-num-seqs", "4", "--enable-prefix-caching"],
         "R2 NVFP4 14B", ro=False),
    vllm("vllm_nemotron3_nano_30b_nvfp4", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4", NEW_ENV,
         ["--trust-remote-code", "--gpu-memory-utilization", "0.40", "--max-model-len", "16384", "--max-num-seqs", "4"],
         "R2 NVFP4 MoE 30B-A3B (hybrid Mamba)", ro=False),
    vllm("vllm_nemotron35_lightning_nvfp4", "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4", NEW_ENV,
         ["--trust-remote-code", "--gpu-memory-utilization", "0.40", "--max-model-len", "16384", "--max-num-seqs", "4"],
         "R2 NVFP4 MoE 30B-A3B", ro=False),
    vllm("vllm_gemma4_26b_a4b_nvfp4", "nvidia/Gemma-4-26B-A4B-NVFP4", NEW_ENV,
         ["--trust-remote-code", "--gpu-memory-utilization", "0.40", "--max-model-len", "16384", "--max-num-seqs", "4"],
         "R2 NVFP4 MoE 26B-A4B", think="omit", ro=False),
    vllm("vllm_qwen36_35b_fp8", "Qwen/Qwen3.6-35B-A3B-FP8", NEW_ENV,
         ["--trust-remote-code", "--kv-cache-dtype", "fp8", "--max-model-len", "32768", "--enable-prefix-caching",
          "--reasoning-parser", "qwen3", "--gpu-memory-utilization", "0.45", "--max-num-seqs", "4"],
         "R2 FP8 (same model as the NVFP4 winner)", ro=False),
    vllm("vllm_qwen36_27b_fp8", "Qwen/Qwen3.6-27B-FP8", NEW_ENV,
         ["--trust-remote-code", "--kv-cache-dtype", "fp8", "--max-model-len", "32768", "--enable-prefix-caching",
          "--reasoning-parser", "qwen3", "--gpu-memory-utilization", "0.45", "--max-num-seqs", "4"],
         "R2 FP8 dense 27B", ro=False),
    vllm("vllm_qwen3_30b_a3b_fp8", "Qwen/Qwen3-30B-A3B-Instruct-2507-FP8", NEW_ENV,
         ["--gpu-memory-utilization", "0.40", "--max-model-len", "16384", "--max-num-seqs", "4", "--enable-prefix-caching"],
         "R2 FP8 MoE 30B-A3B", think="omit", ro=False),
    vllm("vllm_qwen3_8b_fp8", "Qwen/Qwen3-8B-FP8", NEW_ENV,
         ["--gpu-memory-utilization", "0.30", "--max-model-len", "16384", "--max-num-seqs", "4", "--enable-prefix-caching"],
         "R2 FP8 8B", ro=False),
    vllm("vllm_qwen3_4b_fp8", "Qwen/Qwen3-4B-Instruct-2507-FP8", NEW_ENV,
         ["--gpu-memory-utilization", "0.30", "--max-model-len", "16384", "--max-num-seqs", "4", "--enable-prefix-caching"],
         "R2 FP8 4B", think="omit", ro=False),
    vllm("vllm_llama31_8b_fp8", "RedHatAI/Meta-Llama-3.1-8B-Instruct-FP8", NEW_ENV,
         ["--gpu-memory-utilization", "0.30", "--max-model-len", "16384", "--max-num-seqs", "4", "--enable-prefix-caching"],
         "R2 FP8 8B", think="omit", ro=False),
    vllm("vllm_nemotron3_nano_30b_fp8", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-FP8", NEW_ENV,
         ["--trust-remote-code", "--gpu-memory-utilization", "0.40", "--max-model-len", "16384", "--max-num-seqs", "4"],
         "R2 FP8 MoE 30B-A3B (hybrid Mamba)", ro=False),
    vllm("vllm_qwen35_122b_nvfp4", "nvidia/Qwen3.5-122B-A10B-NVFP4", NEW_ENV,
         ["--trust-remote-code", "--gpu-memory-utilization", "0.85", "--max-model-len", "8192", "--max-num-seqs", "4"],
         "R2 NVFP4 MoE 122B-A10B (largest that fits)", ro=False),
    # --- round 3: small models (<= ~4B) ---
    ollama("ollama_qwen3_1p7b_small", "qwen3:1.7b", OL_TUNED, "R3 small model", think="false"),
    ollama("ollama_qwen35_0p8b_small", "qwen3.5:0.8b", OL_TUNED, "R3 small model", think="false"),
    ollama("ollama_qwen35_2b_small", "qwen3.5:2b", OL_TUNED, "R3 small model", think="false"),
    ollama("ollama_qwen35_4b_small", "qwen3.5:4b", OL_TUNED, "R3 small model", think="false"),
    ollama("ollama_gemma3_1b_small", "gemma3:1b", OL_TUNED, "R3 small model", think="omit"),
    ollama("ollama_gemma3_270m_small", "gemma3:270m", OL_TUNED, "R3 small model", think="omit"),
    ollama("ollama_gemma3n_e2b_small", "gemma3n:e2b", OL_TUNED, "R3 small model", think="omit"),
    ollama("ollama_llama32_1b_small", "llama3.2:1b", OL_TUNED, "R3 small model", think="omit"),
    ollama("ollama_smollm2_1p7b_small", "smollm2:1.7b", OL_TUNED, "R3 small model", think="omit"),
    ollama("ollama_smollm2_360m_small", "smollm2:360m", OL_TUNED, "R3 small model", think="omit"),
    ollama("ollama_qwen25_0p5b_small", "qwen2.5:0.5b", OL_TUNED, "R3 small model", think="omit"),
    ollama("ollama_qwen25_1p5b_small", "qwen2.5:1.5b", OL_TUNED, "R3 small model", think="omit"),
    ollama("ollama_qwen25_3b_small", "qwen2.5:3b", OL_TUNED, "R3 small model", think="omit"),
    ollama("ollama_tinyllama_1p1b_small", "tinyllama:1.1b", OL_TUNED, "R3 small model", think="omit"),
    ollama("ollama_granite4_micro_small", "granite4:micro", OL_TUNED, "R3 small model", think="omit"),
    ollama("ollama_granite4_350m_small", "granite4:350m", OL_TUNED, "R3 small model", think="omit"),
    ollama("ollama_phi35_3p8b_small", "phi3.5:3.8b", OL_TUNED, "R3 small model", think="omit"),
    ollama("ollama_exaone35_2p4b_small", "exaone3.5:2.4b", OL_TUNED, "R3 small model", think="omit"),
    vllm("vllm_qwen3_0p6b_fp8_small", "Qwen/Qwen3-0.6B-FP8", NEW_ENV,
         ["--gpu-memory-utilization", "0.20", "--max-model-len", "16384", "--max-num-seqs", "4", "--enable-prefix-caching"],
         "R3 small FP8", ro=False),
    vllm("vllm_qwen3_1p7b_fp8_small", "Qwen/Qwen3-1.7B-FP8", NEW_ENV,
         ["--gpu-memory-utilization", "0.20", "--max-model-len", "16384", "--max-num-seqs", "4", "--enable-prefix-caching"],
         "R3 small FP8", ro=False),
    # --- round 4: small 4-bit FP4 / NVFP4 community + vendor quantisations (<= ~4B) ---
    vllm("vllm_qwen3_0p6b_fp4_nv4s", "NVFP4/Qwen3-0.6B-FP4", NEW_ENV,
         ["--gpu-memory-utilization", "0.20", "--max-model-len", "16384", "--max-num-seqs", "4", "--enable-prefix-caching"],
         "R4 small FP4", ro=False),
    vllm("vllm_qwen25_1p5b_w4a4fp4_nv4s", "pebeto/Qwen2.5-1.5B-Instruct-w4a4-fp4", NEW_ENV,
         ["--gpu-memory-utilization", "0.20", "--max-model-len", "16384", "--max-num-seqs", "4", "--enable-prefix-caching"],
         "R4 small W4A4 FP4", think="omit", ro=False),
    vllm("vllm_qwen25_3b_fp4_nv4s", "JongYeop/Qwen2.5-3B-Instruct-FP4-W4A4", NEW_ENV,
         ["--gpu-memory-utilization", "0.25", "--max-model-len", "16384", "--max-num-seqs", "4", "--enable-prefix-caching"],
         "R4 small NVFP4 W4A4", think="omit", ro=False),
    vllm("vllm_phi4_mini_w4a4fp4_nv4s", "pebeto/Phi-4-mini-instruct-w4a4-fp4", NEW_ENV,
         ["--gpu-memory-utilization", "0.25", "--max-model-len", "16384", "--max-num-seqs", "4", "--enable-prefix-caching"],
         "R4 small W4A4 FP4", think="omit", ro=False),
    vllm("vllm_gemma3_4b_w4a4fp4_nv4s", "pebeto/gemma-3-4b-it-w4a4-fp4", NEW_ENV,
         ["--gpu-memory-utilization", "0.25", "--max-model-len", "8192", "--max-num-seqs", "4"],
         "R4 small W4A4 FP4", think="omit", ro=False),
    vllm("vllm_qwen35_4b_nvfp4_nv4s", "QUASAR-QAT/Qwen3.5-4B-QUASAR-NVFP4-W4A4", NEW_ENV,
         ["--trust-remote-code", "--gpu-memory-utilization", "0.25", "--max-model-len", "16384", "--max-num-seqs", "4"],
         "R4 small NVFP4 W4A4", ro=False),
    ollama("ollama_exaone35_2p4b_base_small", "exaone3.5:2.4b", OL_BASE, "R3 rerun: q8 KV cache unsupported by this model", think="omit"),
    # --- round 6: llama.cpp (build with NVFP4 + MTP support) ---
    llama("llamacpp_q4kxl_nomtp", "Qwen3.6-35B-A3B-GGUF-Q4_K_XL", "unsloth/Qwen3.6-35B-A3B-MTP-GGUF",
          "Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf", LL_ARGS, "R6 llama.cpp unsloth UD-Q4_K_XL, MTP off"),
    llama("llamacpp_q4kxl_mtp", "Qwen3.6-35B-A3B-GGUF-Q4_K_XL-MTP", "unsloth/Qwen3.6-35B-A3B-MTP-GGUF",
          "Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf", LL_ARGS + LL_MTP, "R6 llama.cpp unsloth UD-Q4_K_XL, MTP draft on"),
    llama("llamacpp_mxfp4_nomtp", "Qwen3.6-35B-A3B-GGUF-MXFP4", "unsloth/Qwen3.6-35B-A3B-MTP-GGUF",
          "Qwen3.6-35B-A3B-MXFP4_MOE.gguf", LL_ARGS, "R6 llama.cpp unsloth MXFP4_MOE, MTP off"),
    llama("llamacpp_mxfp4_mtp", "Qwen3.6-35B-A3B-GGUF-MXFP4-MTP", "unsloth/Qwen3.6-35B-A3B-MTP-GGUF",
          "Qwen3.6-35B-A3B-MXFP4_MOE.gguf", LL_ARGS + LL_MTP, "R6 llama.cpp unsloth MXFP4_MOE, MTP draft on"),
    llama("llamacpp_nvfp4_nomtp", "Qwen3.6-35B-A3B-GGUF-NVFP4", "michaelw9999/Qwen3.6-35B-A3B-NVFP4-MTP-GGUF",
          "Qwen3.6-35B-A3B-NVFP4-MTP-HQ.gguf", LL_ARGS, "R6 llama.cpp community NVFP4 (HQ), MTP off"),
    llama("llamacpp_nvfp4_mtp", "Qwen3.6-35B-A3B-GGUF-NVFP4-MTP", "michaelw9999/Qwen3.6-35B-A3B-NVFP4-MTP-GGUF",
          "Qwen3.6-35B-A3B-NVFP4-MTP-HQ.gguf", LL_ARGS + LL_MTP, "R6 llama.cpp community NVFP4 (HQ), MTP draft on"),
]
JUDGE = SPECS[0]

LOG = None


def log(msg):
    line = f"{time.strftime('%F %T')} {msg}"
    print(line, flush=True)
    if LOG:
        with open(LOG, "a") as f:
            f.write(line + "\n")


def sh(cmd, check=False, timeout=None):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise RuntimeError(f"{cmd}\n{r.stderr[-500:]}")
    return r


def stop_all_models():
    sh("docker rm -f bench-vllm bench-ollama bench-llama >/dev/null 2>&1; docker stop " + " ".join(ORIGINALS) + " >/dev/null 2>&1")
    time.sleep(6)


def ensure_downloaded(spec):
    """Blocking, resumable HF download on the host (a prefetcher may already be fetching; hub file locks serialise)."""
    if spec["kind"] == "llamacpp":  # prefetch_gguf.py downloads and verifies; it drops <file>.ok when done
        marker = Path(LLAMA_DIR) / (spec["file"] + ".ok")
        log(f"waiting for GGUF {spec['file']}")
        for _ in range(6 * 3600 // 10):
            if marker.exists():
                return
            time.sleep(10)
        return
    if spec["kind"] != "vllm" or spec.get("ro", True):
        return
    log(f"ensuring weights for {spec['model']}")
    sh(f"{PY} -c \"from huggingface_hub import snapshot_download as s; s('{spec['model']}', cache_dir='{HF}/hub')\" "
       f">> {HOME}/download.log 2>&1", timeout=6 * 3600)


def start(spec):
    env = " ".join(f"-e {k}={v}" for k, v in spec["env"].items())
    if spec["kind"] == "llamacpp":
        cmd = (f"docker run -d --name bench-llama --gpus all -p {LLAMA_PORT}:8080 -v {LLAMA_DIR}:/models:ro "
               f"{LLAMA_IMAGE} -m /models/{spec['file']} --alias {spec['model']} --host 0.0.0.0 --port 8080 "
               + " ".join(spec["args"]))
    elif spec["kind"] == "vllm":
        mode = "ro" if spec.get("ro") else "rw"
        cmd = (f"docker run -d --name bench-vllm --gpus all --ipc=host --shm-size 8g -p {VLLM_PORT}:8000 "
               f"-v {HF}:/root/.cache/huggingface:{mode} {env} vllm/vllm-openai:latest "
               f"{spec['model']} --host 0.0.0.0 --port 8000 " + " ".join(spec["args"]))
    else:
        cmd = (f"docker run -d --name bench-ollama --gpus all -p {OLLAMA_PORT}:11434 -v ollama_data:/root/.ollama "
               f"-e OLLAMA_HOST=0.0.0.0:11434 {env} ollama/ollama:latest")
    sh(cmd, check=True)


def url(spec):
    port = {"vllm": VLLM_PORT, "ollama": OLLAMA_PORT, "llamacpp": LLAMA_PORT}[spec["kind"]]
    return f"http://localhost:{port}"


def cname(spec):
    return {"vllm": "bench-vllm", "ollama": "bench-ollama", "llamacpp": "bench-llama"}[spec["kind"]]


def wait_ready(spec, timeout=1800):
    t0, name = time.time(), cname(spec)
    path = "/api/tags" if spec["kind"] == "ollama" else "/v1/models"
    while time.time() - t0 < timeout:
        if sh(f"docker inspect -f '{{{{.State.Running}}}}' {name}").stdout.strip() != "true":
            return False
        if sh(f"curl -sf -m 3 {url(spec)}{path} >/dev/null").returncode == 0:
            break
        time.sleep(5)
    else:
        return False
    if spec["kind"] == "ollama":  # pull if missing, then load into memory
        if spec["model"] not in sh(f"curl -s {url(spec)}/api/tags").stdout:
            log(f"pulling {spec['model']}")
            sh(f"docker exec bench-ollama ollama pull {spec['model']}", timeout=3600)
        body = json.dumps({"model": spec["model"], "prompt": "hi", "stream": False, "keep_alive": "30m",
                           "options": {"num_ctx": 8192, "num_predict": 8}})
        r = sh(f"curl -s -m 900 {url(spec)}/api/generate -d '{body}'")
        return '"done":true' in r.stdout.replace(" ", "")
    return True


class Sampler(threading.Thread):
    """1 Hz hardware sampling: GPU util/power/temp/clock, used system memory (unified on GB10), CPU%."""

    def __init__(self, path):
        super().__init__(daemon=True)
        self.path, self.stop_ev = path, threading.Event()

    @staticmethod
    def cpu():
        v = list(map(int, open("/proc/stat").readline().split()[1:]))
        return sum(v), v[3] + v[4]

    def run(self):
        tot0, idle0 = self.cpu()
        with open(self.path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts", "gpu_util", "power_w", "temp_c", "sm_mhz", "mem_used_mb", "cpu_pct"])
            while not self.stop_ev.is_set():
                q = sh("nvidia-smi --query-gpu=utilization.gpu,power.draw,temperature.gpu,clocks.sm "
                       "--format=csv,noheader,nounits").stdout.strip().split(",")
                mem = sh("free -m | awk '/Mem:/{print $3}'").stdout.strip()
                tot, idle = self.cpu()
                cpu = 100.0 * (1 - (idle - idle0) / max(tot - tot0, 1))
                tot0, idle0 = tot, idle
                w.writerow([f"{time.time():.1f}"] + [x.strip() for x in q] + [mem, f"{cpu:.1f}"])
                f.flush()
                self.stop_ev.wait(1.0)

    def finish(self):
        self.stop_ev.set()
        self.join(5)


def phase(results, spec, name, t0, t1):
    with open(results / "phases.csv", "a") as f:
        f.write(f"{spec['name']},{name},{t0:.1f},{t1:.1f}\n")


def run_spec(spec, results):
    d = results / spec["name"]
    d.mkdir(parents=True, exist_ok=True)
    log(f"=== {spec['name']}  ({spec['note']})")
    ensure_downloaded(spec)
    stop_all_models()
    start(spec)
    if not wait_ready(spec):
        (d / "FAILED.log").write_text(sh(f"docker logs --tail 80 {cname(spec)} 2>&1").stdout)
        log(f"!! {spec['name']} failed to start; skipped")
        return
    time.sleep(5)
    sampler = Sampler(d / "hw.csv")
    sampler.start()
    script = "benchmark_ollama.py" if spec["kind"] == "ollama" else "benchmark_vllm.py"  # OpenAI protocol
    backend = "ollama" if spec["kind"] == "ollama" else "vllm"
    try:
        t0 = time.time()
        sh(f"{PY} {HOME}/accuracy_test.py --backend {backend} --url {url(spec)} --model {spec['model']} "
           f"--think {spec['think'] if spec['think'] != 'omit' else 'default'} --max-tokens {spec.get('acc_tok', 512)} "
           f"> {d}/accuracy.log 2>&1", timeout=3600)
        for f in sorted((HOME / "outputs/accuracy_test").glob("*.json")):  # move report into spec dir
            f.rename(d / f"accuracy.json")
        for f in (HOME / "outputs/accuracy_test").glob("*.md"):
            f.rename(d / "accuracy.md")
        phase(results, spec, "accuracy", t0, time.time())
        if spec["kind"] == "llamacpp":  # does speculative decoding keep answers reproducible?
            sh(f"{PY} {HOME}/determinism_test.py --url {url(spec)} --model {spec['model']} "
               f"--out {d}/determinism.json > {d}/determinism.log 2>&1", timeout=1800)
        for prof, pfile in PROFILES.items():
            t0 = time.time()
            extra = f"--prompt {pfile}" + (" --unique-prompts" if prof == "rag" else "")
            extra += " --ctx 8192"
            cmd = (f"{PY} {HOME}/{script} --url {url(spec)} --models {spec['model']} --levels {LEVELS} "
                   f"--users {USERS} --requests {REQS} --warmup 2 --max-tokens {MAXTOK} --timeout 300 "
                   f"--think {spec['think']} {extra} --output-html {d}/bench_{prof}.html "
                   f"--output-json {d}/bench_{prof}.json --output-csv {d}/bench_{prof}.csv > {d}/bench_{prof}.log 2>&1")
            sh(cmd, timeout=3600)
            phase(results, spec, f"bench_{prof}", t0, time.time())
            log(f"   {prof} done in {time.time() - t0:.0f}s")
    except Exception as exc:
        log(f"!! {spec['name']} error: {exc}")
    finally:
        if spec["kind"] == "llamacpp":  # speculative-decoding / throughput lines llama-server printed
            (d / "llama_stats.txt").write_text(sh(
                f"docker logs {cname(spec)} 2>&1 | grep -i -E 'accept|draft|eval time|tokens per second' | tail -40").stdout)
        sampler.finish()
        (d / "docker_logs_tail.txt").write_text(
            sh(f"docker logs --tail 40 {cname(spec)} 2>&1").stdout)
        sh("docker rm -f bench-vllm bench-ollama bench-llama >/dev/null 2>&1")


def judge_all(results):
    log("=== judge phase (qwen36 alone)")
    stop_all_models()
    start(JUDGE)
    if not wait_ready(JUDGE):
        log("!! judge model failed to start")
        return
    for f in sorted(results.glob("*/accuracy.json")):
        sh(f"{PY} {HOME}/accuracy_test.py --judge-only {f} --judge-url {url(JUDGE)} "
           f"--judge-model {JUDGE['model']} > {f.parent}/judge.log 2>&1", timeout=3600)
        log(f"   judged {f.parent.name}")
    sh("docker rm -f bench-vllm >/dev/null 2>&1")


def restore(only=None):
    log("restoring original containers" + (f" (only {only})" if only else ""))
    sh("docker rm -f bench-vllm bench-ollama bench-llama >/dev/null 2>&1")
    sh("docker update --restart=unless-stopped " + " ".join(c for c in ORIGINALS if c not in MANAGED))
    # start one by one so unified memory is not oversubscribed during load
    for c in only or ["ollama", "clef-flash", "vllm-qwen36", "vllm-gpt-oss-20b", "vllm-gemma-12b"]:
        sh(f"docker start {c}")
        time.sleep(90 if c.startswith("vllm") else 5)
    log(sh("docker ps --format '{{.Names}} {{.Status}}'").stdout)


def main():
    global LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--results", default=str(HOME / "results" / time.strftime("%Y%m%d_%H%M%S")))
    ap.add_argument("--no-restore", action="store_true")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--restore-only", default="", help="comma list: start only these original containers at the end")
    a = ap.parse_args()
    results = Path(a.results)
    results.mkdir(parents=True, exist_ok=True)
    LOG = results / "suite.log"
    (results / "specs.json").write_text(json.dumps(SPECS, indent=2))
    (results / "hardware.txt").write_text(sh("nvidia-smi; lscpu | head -20; free -m; docker --version").stdout)
    only = [s for s in a.only.split(",") if s]
    try:
        for spec in SPECS:
            if only and not any(o in spec["name"] for o in only):
                continue
            run_spec(spec, results)
        if not a.no_judge:
            judge_all(results)
    except BaseException as exc:
        log(f"!! suite aborted: {exc!r}")
    finally:
        if not a.no_restore:
            restore([c for c in a.restore_only.split(",") if c] or None)
        sh(f"chown -R uday {HOME}")
        log("SUITE FINISHED")


if __name__ == "__main__":
    main()
