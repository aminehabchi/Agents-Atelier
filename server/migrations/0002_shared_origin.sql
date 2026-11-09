-- Where a shared agent or team last came from: 'migration' (a data migration wrote it, so a later one may update it)
-- or 'app' (an admin created or edited it in the app, so migrations leave it alone).
ALTER TABLE agents ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'app';
ALTER TABLE teams  ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'app';

-- Shared agents and teams an admin deleted: data migrations never bring them back.
CREATE TABLE IF NOT EXISTS shared_removed (
    kind       text NOT NULL CHECK (kind IN ('agent', 'team')),
    slug       text NOT NULL,
    removed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, slug)
);

-- Before migrations, shared agents and teams were copied from agents/*.md (on first start or by sync-agents), so the
-- ones already here count as migration data that later agent migrations may refresh.
UPDATE agents SET origin = 'migration' WHERE owner_id IS NULL;
UPDATE teams  SET origin = 'migration' WHERE owner_id IS NULL;
