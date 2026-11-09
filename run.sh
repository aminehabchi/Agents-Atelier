#!/usr/bin/env bash
# Start Agents Atelier (default http://localhost:8001).
# Settings live in .env — copy .env.example to .env and edit it. Variables set on the command line win,
# e.g.  PORT=9000 ./run.sh   or   AGENT_MODEL=sonnet ./run.sh
set -e
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q fastapi "uvicorn[standard]" asyncpg; }
.venv/bin/python -c "import asyncpg" 2>/dev/null || .venv/bin/pip install -q asyncpg
# Postgres holds accounts, agents and teams. Start it if Docker is here and it isn't running yet.
if command -v docker >/dev/null 2>&1 && [ -z "${DATABASE_URL:-}" ] && ! grep -qs '^DATABASE_URL=..*' .env; then
  docker compose -f database/docker-compose.yaml up -d --wait >/dev/null || echo "Couldn't start Postgres with Docker. See database/docker-compose.yaml."
fi
[ -f .env ] || [ ! -f .env.example ] || echo "Tip: copy .env.example to .env to change settings."
ROOT="$(pwd)"
cd server
exec "$ROOT/.venv/bin/python" serve.py
