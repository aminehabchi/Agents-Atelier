-- The marketplace: agents and teams as freelancers people can hire.
--
-- listing      private (only the owner sees it) · pending (waiting for an admin) · listed (in the catalogue) · rejected
-- category     where it shows in the catalogue (agents: planning, writing, design, code, growth, quality;
--              teams: build, improve, add); empty = picked from the agent's id for the shared ones
-- listing_note what the admin wrote when rejecting it
-- hired_from   the catalogue agent or team this one was hired (copied) from: hires are counted on the original
ALTER TABLE agents ADD COLUMN IF NOT EXISTS listing      text NOT NULL DEFAULT 'private'
    CHECK (listing IN ('private', 'pending', 'listed', 'rejected'));
ALTER TABLE agents ADD COLUMN IF NOT EXISTS category     text NOT NULL DEFAULT '';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS listing_note text NOT NULL DEFAULT '';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS listed_at    timestamptz;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS hired_from   bigint REFERENCES agents(pk) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS agents_hired_from ON agents(hired_from);
CREATE INDEX IF NOT EXISTS agents_listing ON agents(listing);

ALTER TABLE teams ADD COLUMN IF NOT EXISTS listing      text NOT NULL DEFAULT 'private'
    CHECK (listing IN ('private', 'pending', 'listed', 'rejected'));
ALTER TABLE teams ADD COLUMN IF NOT EXISTS category     text NOT NULL DEFAULT '';
ALTER TABLE teams ADD COLUMN IF NOT EXISTS listing_note text NOT NULL DEFAULT '';
ALTER TABLE teams ADD COLUMN IF NOT EXISTS listed_at    timestamptz;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS hired_from   bigint REFERENCES teams(pk) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS teams_hired_from ON teams(hired_from);

-- A thumbs up or down (and an optional comment) from a project's owner for one agent's work in that project.
CREATE TABLE IF NOT EXISTS agent_ratings (
    pk         bigserial PRIMARY KEY,
    agent_pk   bigint NOT NULL REFERENCES agents(pk) ON DELETE CASCADE,
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project    text NOT NULL,
    value      smallint NOT NULL CHECK (value IN (-1, 1)),
    comment    text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (agent_pk, user_id, project)
);
CREATE INDEX IF NOT EXISTS agent_ratings_agent ON agent_ratings(agent_pk);
