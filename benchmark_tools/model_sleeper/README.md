# model-sleeper

Frees the RAM/GPU memory of idle model containers (vLLM, llama.cpp) and wakes them on the first request.
Ollama already does this itself (`OLLAMA_KEEP_ALIVE`, 30 m on the Spark).

```
client ──▶ :8008 (sleeper) ──▶ 127.0.0.1:18008 ──▶ container vllm-qwen36
```

- A request to a sleeping model starts the container and waits until it answers (vLLM Qwen3.6-35B: ~2.5 min,
  gpt-oss-20b: ~2.5 min); clients need a timeout of at least 5 minutes for the first request after a sleep.
- A model sleeps (`docker stop`, memory freed) after `idle_seconds` without traffic (default 15 min).
  Streaming answers count as traffic. A model started by something else (a benchmark run) gets a full idle window.
- Plain TCP forwarding: works for streamed answers and any HTTP API. Health checks (`/v1/models`) also wake the model;
  use the status endpoint instead.

```
curl http://127.0.0.1:8099/status             # who is awake, seconds until sleep (never wakes anything)
curl -X POST http://127.0.0.1:8099/wake/qwen36
curl -X POST http://127.0.0.1:8099/sleep/qwen36
journalctl -u model-sleeper -f
```

Install (on the server, root): `sudo ./install.sh` — copies `sleeper.py` to `/opt/model-sleeper`, writes `/etc/model-sleeper.json`
(from `config.example.json`), recreates the containers with `docker create` (same image, command, env, mounts, GPU; published
on `127.0.0.1:<backend_port>`, `--restart no`; the old container stays as `<name>.pre-sleeper`) and enables the systemd unit.
Change `idle_seconds` in `/etc/model-sleeper.json` and `sudo systemctl restart model-sleeper`.
Roll back: `sudo systemctl disable --now model-sleeper; docker rm <name>; docker rename <name>.pre-sleeper <name>; docker update --restart unless-stopped <name>; docker start <name>`.
