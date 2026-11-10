"""Postgres: one connection pool, and the migrations that build and fill it.

Tables
  users        accounts: email, password hash, role (admin · user · unconfirmed), profile, personal agent order
  sessions     login sessions (only a hash of the cookie token is stored)
  email_codes  the pending 6-digit confirmation code of each unconfirmed account (hashed)
  reset_codes  the pending 6-digit password reset code of an account (hashed), and its daily limits
  brand_kits   an account's logo, colours and fonts, reused in new projects
  agents       every agent: owner_id NULL = shared (only admins change it), otherwise that user's own agent
  teams        saved teams, same ownership rule (the built-in teams are computed, not stored)
  projects, project_requests, agent_runs, project_events, versions, files   everything about projects and files;
               the files themselves are in STORAGE_DIR (see storage.py)

  shared_removed  shared agents and teams an admin deleted (agent migrations don't bring them back)
  schema_migrations  the migrations applied so far

Everything is created and filled by the migrations in server/migrations/, applied on startup (see migrate.py):
the tables, then the shared agents and teams. To change those, edit agents/*.md or agents/_presets.json and run
`manage.py make-agents-migration`; agents an admin edited in the app are never overwritten.
"""

import logging

import asyncpg

import config
import migrate

log = logging.getLogger("agents-atelier.db")
pool: asyncpg.Pool | None = None
MAX_TEAM_PROMPT = 2000   # a team's starting prompt



async def init():
    """Open the pool and apply any pending migration (tables, then the shared agents and teams)."""
    global pool
    try:
        pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=10, command_timeout=30)
    except (OSError, asyncpg.PostgresError) as e:
        raise SystemExit(f"Can't connect to Postgres at {_safe_url(config.DATABASE_URL)} ({e}).\n"
                         "Start it with:  docker compose -f database/docker-compose.yaml up -d\n"
                         "or set DATABASE_URL in .env.")
    async with pool.acquire() as con:
        applied = await migrate.apply_all(con)
        import storage                    # agents' instructions live in files: move any a migration just wrote
        moved = await storage.export_instructions(con)
        await storage.forget_deleted_agents(con)
    if moved:
        log.info("Wrote the instructions of %d agents to %s", moved, storage.AGENTS)
    if applied:
        log.info("Database migrated: %s", ", ".join(applied))


async def close():
    global pool
    if pool:
        await pool.close()
        pool = None


def _safe_url(url: str) -> str:
    """The URL without its password, for messages."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        return f"{scheme}://{creds.split(':', 1)[0]}:***@{host}"
    return url
