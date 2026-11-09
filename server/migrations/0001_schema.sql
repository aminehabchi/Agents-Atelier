-- The tables as they were when migrations were introduced. Every statement is idempotent,
-- so a database created before migrations existed takes this one without change.

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
