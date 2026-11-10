-- Everything about a project lives in the database; only files stay on disk (STORAGE_DIR, see storage.py).
-- Times are Unix timestamps (seconds, as the server uses them everywhere).

CREATE TABLE IF NOT EXISTS projects (
    id          uuid PRIMARY KEY,
    owner_id    uuid,                                   -- NULL: a project from before accounts (admins only)
    name        text NOT NULL,
    description text NOT NULL DEFAULT '',
    pages       jsonb NOT NULL DEFAULT '[]',            -- [{title, file}]
    brief       jsonb NOT NULL DEFAULT '{}',            -- the project questions' answers
    brand       jsonb NOT NULL DEFAULT '{}',            -- the brand kit's colours and fonts (the logo is an asset)
    assets      jsonb NOT NULL DEFAULT '[]',            -- the user's images in site/assets: [{path, kind, note, size}]
    agents      text[] NOT NULL DEFAULT '{}',           -- the agents switched on
    showcase    boolean NOT NULL DEFAULT false,         -- shown in the portfolios of the agents that built it
    run         jsonb NOT NULL DEFAULT '{}',            -- the current or last run (for Continue / Start again)
    extra       jsonb NOT NULL DEFAULT '{}',            -- anything else the server keeps about the project
    created     double precision NOT NULL,
    updated     double precision NOT NULL
);
CREATE INDEX IF NOT EXISTS projects_owner ON projects(owner_id);

-- The requests sent to the team, in order
CREATE TABLE IF NOT EXISTS project_requests (
    project_id uuid NOT NULL,
    n          integer NOT NULL,
    text       text NOT NULL,
    ts         double precision NOT NULL,
    PRIMARY KEY (project_id, n)
);

-- One row per agent run: time, cost, tokens, model, and the Critic's score
CREATE TABLE IF NOT EXISTS agent_runs (
    id         bigserial PRIMARY KEY,
    project_id uuid NOT NULL,
    agent      text NOT NULL,
    status     text NOT NULL,
    ts         double precision NOT NULL,
    run        double precision,                        -- the start time of the team run it belongs to
    request    text NOT NULL DEFAULT '',
    seconds    double precision NOT NULL DEFAULT 0,
    cost       double precision,
    tokens_in  integer,
    tokens_out integer,
    model      text NOT NULL DEFAULT '',
    score      double precision,
    extra      jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS agent_runs_project ON agent_runs(project_id, ts);

-- The chat: every event the studio replays, numbered per project
CREATE TABLE IF NOT EXISTS project_events (
    project_id uuid NOT NULL,
    seq        integer NOT NULL,
    type       text NOT NULL,
    ts         double precision NOT NULL,
    data       jsonb NOT NULL,
    PRIMARY KEY (project_id, seq)
);

-- Saved versions of a project's site (the files are in works/<project>/versions/<id>/)
CREATE TABLE IF NOT EXISTS versions (
    id         uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    n          integer NOT NULL,
    label      text NOT NULL DEFAULT '',
    kind       text NOT NULL DEFAULT 'manual',
    agent      text,
    request    text NOT NULL DEFAULT '',
    ts         double precision NOT NULL,
    files      integer NOT NULL DEFAULT 0,
    size       bigint NOT NULL DEFAULT 0,
    hash       text NOT NULL DEFAULT '',
    UNIQUE (project_id, n)
);

-- Every file in STORAGE_DIR, each with its own id
--   area: site · version · upload · check (in works/<project>/) or agent (agents/<agent>/)
CREATE TABLE IF NOT EXISTS files (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid,
    agent_id   uuid,
    area       text NOT NULL CHECK (area IN ('site', 'version', 'upload', 'check', 'agent')),
    version_id uuid,                                    -- the version (area 'version') or browser check (area 'check')
    path       text NOT NULL,                           -- relative to its folder
    size       bigint NOT NULL DEFAULT 0,
    sha256     text NOT NULL DEFAULT '',
    updated    double precision NOT NULL,
    UNIQUE NULLS NOT DISTINCT (project_id, agent_id, area, version_id, path)
);
CREATE INDEX IF NOT EXISTS files_project ON files(project_id, area);

-- Agents get a uuid: their instructions are the file agents/<uuid>/instructions.md
ALTER TABLE agents ADD COLUMN IF NOT EXISTS uuid uuid NOT NULL DEFAULT gen_random_uuid();
CREATE UNIQUE INDEX IF NOT EXISTS agents_uuid ON agents(uuid);
ALTER TABLE agents ALTER COLUMN role SET DEFAULT '';
