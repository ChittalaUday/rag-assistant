#!/usr/bin/env python3
"""Download model weights ahead of the suite (network is the bottleneck): python prefetch.py repo1 repo2 ..."""
import sys
from huggingface_hub import snapshot_download

for repo in sys.argv[1:]:
    print("downloading", repo, flush=True)
    try:
        snapshot_download(repo, cache_dir="/home/uday/.cache/huggingface/hub")
        print("done", repo, flush=True)
    except Exception as exc:
        print("FAILED", repo, exc, flush=True)
