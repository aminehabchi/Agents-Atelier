"""Postgres: one connection pool, the schema, and the first fill of the shared agents and teams.

Tables
  users        accounts: email, password hash, role (admin · user · unconfirmed), profile, personal agent order
  sessions     login sessions (only a hash of the cookie token is stored)
  email_codes  the pending 6-digit confirmation code of each unconfirmed account (hashed)
  reset_codes  the pending 6-digit password reset code of an account (hashed), and its daily limits
  brand_kits   an account's logo, colours and fonts, reused in new projects
  agents       every agent: owner_id NULL = shared (only admins change it), otherwise that user's own agent
  teams        saved teams, same ownership rule (the built-in teams are computed, not stored)

The schema is created on startup (CREATE … IF NOT EXISTS). The first time the agents table has no shared agent,
it is filled from the Markdown files in agents/ (and agents/_presets.json for teams).
"""

import logging

import asyncpg

import config

log = logging.getLogger("agents-atelier.db")
pool: asyncpg.Pool | None = None
MAX_TEAM_PROMPT = 2000   # a team's starting prompt

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            uuid PRIMARY KEY,
    email         text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    role          text NOT NULL DEFAULT 'unconfirmed' CHECK (role IN ('admin', 'user', 'unconfirmed')),
    agent_order   text[] NOT NULL DEFAULT '{}',
    failed_logins integer NOT NULL DEFAULT 0,
    locked_until  timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    confirmed_at  timestamptz
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS name     text NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS company  text NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS job_role text NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS use_case text NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral text NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS language text NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS theme    text NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_accepted_at timestamptz;

CREATE TABLE IF NOT EXISTS brand_kits (
    user_id      uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    colors       text[] NOT NULL DEFAULT '{}',
    heading_font text NOT NULL DEFAULT '',
    body_font    text NOT NULL DEFAULT '',
    logo         text NOT NULL DEFAULT '',
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash text PRIMARY KEY,
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS email_codes (
    user_id    uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    code_hash  text NOT NULL,
    expires_at timestamptz NOT NULL,
    attempts   integer NOT NULL DEFAULT 0,
    sent_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reset_codes (
    user_id      uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    code_hash    text,
    expires_at   timestamptz NOT NULL,
    attempts     integer NOT NULL DEFAULT 0,
    sent_at      timestamptz NOT NULL DEFAULT now(),
    window_start timestamptz NOT NULL DEFAULT now(),
    sends        integer NOT NULL DEFAULT 0,
    fails        integer NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS agents (
    pk          bigserial PRIMARY KEY,
    owner_id    uuid REFERENCES users(id) ON DELETE CASCADE,
    slug        text NOT NULL,
    name        text NOT NULL,
    icon        text NOT NULL DEFAULT 'bot',
    color       text NOT NULL DEFAULT '#766a7c',
    is_default  boolean NOT NULL DEFAULT true,
    description text NOT NULL DEFAULT '',
    model       text NOT NULL DEFAULT '',
    tools       text NOT NULL DEFAULT '',
    role        text NOT NULL,
    position    integer NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS agents_owner_slug ON agents ((coalesce(owner_id::text, '')), slug);

CREATE TABLE IF NOT EXISTS teams (
    pk          bigserial PRIMARY KEY,
    owner_id    uuid REFERENCES users(id) ON DELETE CASCADE,
    slug        text NOT NULL,
    name        text NOT NULL,
    description text NOT NULL DEFAULT '',
    agents      text[] NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS teams_owner_slug ON teams ((coalesce(owner_id::text, '')), slug);
ALTER TABLE teams ADD COLUMN IF NOT EXISTS prompt text NOT NULL DEFAULT '';
"""


async def init():
    """Open the pool, create the tables and fill the shared agents the first time."""
    global pool
    try:
        pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=10, command_timeout=30)
    except (OSError, asyncpg.PostgresError) as e:
        raise SystemExit(f"Can't connect to Postgres at {_safe_url(config.DATABASE_URL)} ({e}).\n"
                         "Start it with:  docker compose -f database/docker-compose.yaml up -d\n"
                         "or set DATABASE_URL in .env.")
    async with pool.acquire() as con:
        async with con.transaction():
            await con.execute("SELECT pg_advisory_xact_lock(4242)")   # two servers starting at once don't both seed
            await con.execute(SCHEMA)
            if not await con.fetchval("SELECT 1 FROM agents WHERE owner_id IS NULL LIMIT 1"):
                await seed_from_files(con)


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


async def seed_from_files(con, replace: bool = False) -> int:
    """Copy agents/*.md (and the saved teams in agents/_presets.json) into the shared agents and teams.
    With replace=True, shared agents that exist are overwritten from their file (see manage.py sync-agents)."""
    import agents as store   # local import: agents.py imports this module
    files = store.read_agent_files()
    for i, a in enumerate(files):
        await con.execute(
            """INSERT INTO agents (owner_id, slug, name, icon, color, is_default, description, model, tools, role, position)
               VALUES (NULL, $1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
               ON CONFLICT ((coalesce(owner_id::text, '')), slug) DO """ +
            ("""UPDATE SET name = $2, icon = $3, color = $4, is_default = $5, description = $6, model = $7, tools = $8,
                           role = $9, position = $10, updated_at = now()""" if replace else "NOTHING"),
            a["id"], a["name"], a["icon"], a["color"], a["default"], a["description"], a["model"], a["tools"], a["role"], i)
    for t in store.read_preset_file():
        slug = store.slugify(t.get("id") or t.get("name") or "team")
        await con.execute(
            """INSERT INTO teams (owner_id, slug, name, description, agents, prompt) VALUES (NULL, $1, $2, $3, $4, $5)
               ON CONFLICT ((coalesce(owner_id::text, '')), slug) DO """ +
            ("UPDATE SET name = $2, description = $3, agents = $4, prompt = $5, updated_at = now()" if replace else "NOTHING"),
            slug, str(t.get("name") or slug)[:40], str(t.get("description") or "")[:200], [str(x) for x in t.get("agents") or []],
            str(t.get("prompt") or "")[:MAX_TEAM_PROMPT])
    log.info("Shared agents %s from %s (%d agents)", "synced" if replace else "created", config.AGENTS_DIR, len(files))
    return len(files)

