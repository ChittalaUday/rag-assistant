#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -d .venv || ! -d frontend/node_modules ]]; then
  echo "Run make setup first."
  exit 1
fi
[[ -f backend/.env ]] || cp backend/.env.example backend/.env
[[ -f frontend/.env ]] || cp frontend/.env.example frontend/.env
pg_isready -q || { echo "PostgreSQL is not running (brew services start postgresql@18)."; exit 1; }
mkdir -p data
host=127.0.0.1
# LAN=1: serve the UI and API to this network (others can test). There is no login: anyone who can
# reach this machine can read, upload and delete documents. Ollama and PostgreSQL stay on loopback.
if [[ "${LAN:-0}" == 1 ]]; then
  ip=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')
  [[ -n "$ip" ]] || { echo "No network address found."; exit 1; }
  host=0.0.0.0
  # for this run only (environment variables win over backend/.env)
  export KE_ALLOWED_HOSTS="[\"127.0.0.1\",\"localhost\",\"$ip\"]"
  export KE_CORS_ORIGINS="[\"http://127.0.0.1:5173\",\"http://localhost:5173\",\"http://$ip:5173\"]"
  echo "Open from another device: http://$ip:5173   (API docs: http://$ip:8000/docs)"
fi
PYTHONPATH=backend .venv/bin/uvicorn knowledge_engine.main:app --host "$host" --port 8000 &
backend_pid=$!
cleanup() { kill "$backend_pid" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
npm --prefix frontend run dev -- --host "$host" --port 5173 --strictPort
