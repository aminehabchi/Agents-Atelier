# Agents Atelier: the web app, Claude Code (which every agent runs on) and Chromium (for the browser checks).
#
#   docker compose up -d --build      build and start it with Postgres (see docker-compose.yml)
#
# Claude Code needs to log in once: see "Run it with Docker" in the README.

FROM python:3.14-slim

ARG CLAUDE_CODE_VERSION=latest

# Node.js for Claude Code, Chromium for the browser checks, fonts so screenshots show text (Latin and Arabic)
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm chromium fonts-dejavu-core fonts-noto-core ca-certificates \
 && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force \
 && apt-get purge -y npm && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

# A normal user: the app and the agents never run as root
RUN useradd --create-home --uid 1000 atelier \
 && mkdir -p /data/storage /data/work /home/atelier/.claude \
 && chown -R atelier:atelier /data /home/atelier

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=atelier:atelier server/ server/
COPY --chown=atelier:atelier agents/ agents/
COPY --chown=atelier:atelier .env.example ./

USER atelier
ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8001 \
    STORAGE_DIR=/data/storage \
    ATELIER_WORK=/data/work \
    CLAUDE_BIN=claude \
    CLAUDE_CONFIG_DIR=/home/atelier/.claude \
    CHROME_BIN=chromium \
    CHROME_NO_SANDBOX=true \
    ATELIER_ENV_FILE=/app/no.env

EXPOSE 8001
VOLUME ["/data/storage", "/home/atelier/.claude"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8001\")}/login', timeout=4)"

WORKDIR /app/server
# One process: the server keeps each project's live state (runs, the write queue) in memory
CMD ["python", "serve.py"]
