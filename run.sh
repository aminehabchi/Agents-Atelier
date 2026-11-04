#!/usr/bin/env bash
# Start Agents Atelier (default http://localhost:8001).
# Settings live in .env — copy .env.example to .env and edit it. Variables set on the command line win,
# e.g.  PORT=9000 ./run.sh   or   AGENT_MODEL=sonnet ./run.sh
set -e
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q fastapi "uvicorn[standard]"; }
[ -f .env ] || [ ! -f .env.example ] || echo "Tip: copy .env.example to .env to change settings."
ROOT="$(pwd)"
cd server
exec "$ROOT/.venv/bin/python" serve.py
