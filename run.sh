#!/usr/bin/env bash
# Start Agents Atelier on http://localhost:8000
# Optional: AGENT_MODEL=sonnet ./run.sh   (faster, cheaper agents)
set -e
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q fastapi "uvicorn[standard]"; }
cd server
exec ../.venv/bin/uvicorn main:app --host 127.0.0.1 --port "${PORT:-8001}"
