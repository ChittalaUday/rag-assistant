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
PYTHONPATH=backend .venv/bin/uvicorn knowledge_engine.main:app --host 127.0.0.1 --port 8000 &
backend_pid=$!
cleanup() { kill "$backend_pid" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort
