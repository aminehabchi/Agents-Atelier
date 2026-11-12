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
        # timeout: opening a connection gives up after 10 s (Postgres down or unreachable) instead of hanging for a minute;
        # command_timeout: no query may hold a connection for more than 30 s, so the pool always frees up.
        pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=10, command_timeout=30, timeout=10)
    except (OSError, asyncpg.PostgresError) as e:
        raise SystemExit(f"Can't connect to Postgres at {_safe_url(config.DATABASE_URL)} ({e}).\n"
                         "Start it with:  docker compose -f database/docker-compose.yaml up -d\n"
                         "or set DATABASE_URL in .env.")
    async with pool.acquire() as con:
        applied = await migrate.apply_all(con)
        import storage                    # agents' instructions live in files: move any a migration just wrote
        storage.check_writable()
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


# ---------------------------------------------------------------- telling a database outage from a bug

UNAVAILABLE = "The database isn't reachable right now. Try again in a minute."
BAD_VALUE = ("One of the values you sent can't be saved (it may contain invalid characters or be too large). "
             "Check it and try again.")
_DOWN = (asyncpg.PostgresConnectionError, asyncpg.CannotConnectNowError, asyncpg.TooManyConnectionsError,
         asyncpg.exceptions.AdminShutdownError, asyncpg.exceptions.CrashShutdownError, asyncpg.exceptions.QueryCanceledError)


def _from_driver(exc: BaseException) -> bool:
    """Was this raised inside asyncpg (so a timeout or refused connection is the database's, not a file's or a website's)?"""
    tb = exc.__traceback__
    while tb is not None:
        if "/asyncpg/" in tb.tb_frame.f_code.co_filename.replace("\\", "/"):
            return True
        tb = tb.tb_next
    return False


def unavailable(exc: BaseException) -> bool:
    """Postgres is down, restarting, overloaded or too slow: the request can work again later (503), it isn't a bug."""
    if isinstance(exc, _DOWN):
        return True
    if isinstance(exc, asyncpg.InterfaceError) and any(w in str(exc) for w in ("closed", "closing", "connection")):
        return True
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)) and _from_driver(exc):
        return True
    return False


def bad_value(exc: BaseException) -> bool:
    """A value Postgres refuses (a NUL character, a number out of range, a string too long): the request's fault (400)."""
    return isinstance(exc, asyncpg.DataError)


def _safe_url(url: str) -> str:
    """The URL without its password, for messages."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        return f"{scheme}://{creds.split(':', 1)[0]}:***@{host}"
    return url
