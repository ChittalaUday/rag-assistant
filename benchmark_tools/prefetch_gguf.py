#!/usr/bin/env python3
"""Download GGUF files one at a time (the network is the bottleneck), verify the size on disk and
drop <file>.ok so run_suite.py can start the model. Plain resumable HTTP download (no Xet)."""
import os

os.environ["HF_HUB_DISABLE_XET"] = "1"
from huggingface_hub import HfApi, hf_hub_download  # noqa: E402

DEST = "/home/uday/models/llamacpp"
FILES = [("unsloth/Qwen3.6-35B-A3B-MTP-GGUF", "Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf"),
         ("unsloth/Qwen3.6-35B-A3B-MTP-GGUF", "Qwen3.6-35B-A3B-MXFP4_MOE.gguf"),
         ("michaelw9999/Qwen3.6-35B-A3B-NVFP4-MTP-GGUF", "Qwen3.6-35B-A3B-NVFP4-MTP-HQ.gguf")]
api = HfApi()
for repo, name in FILES:
    want = next(s.size for s in api.model_info(repo, files_metadata=True).siblings if s.rfilename == name)
    path = os.path.join(DEST, name)
    print("downloading", name, f"{want / 1e9:.1f} GB", flush=True)
    try:
        hf_hub_download(repo, name, local_dir=DEST)
        got = os.path.getsize(path) if os.path.exists(path) else -1
        if got != want:
            print("FAILED size", name, got, want, flush=True)
            continue
        open(path + ".ok", "w").write(str(got))
        print("done", name, got, flush=True)
    except Exception as exc:
        print("FAILED", name, exc, flush=True)
