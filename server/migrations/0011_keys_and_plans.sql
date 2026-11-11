-- Each member's own Claude API key (encrypted, see keys.py) and their plan (see plans.py).
ALTER TABLE users ADD COLUMN IF NOT EXISTS claude_key      text NOT NULL DEFAULT '';   -- Fernet-encrypted, '' = none
ALTER TABLE users ADD COLUMN IF NOT EXISTS claude_key_hint text NOT NULL DEFAULT '';   -- sk-ant-…abcd, shown in the profile
ALTER TABLE users ADD COLUMN IF NOT EXISTS claude_key_at   double precision;           -- when it was last checked
ALTER TABLE users ADD COLUMN IF NOT EXISTS plan            text NOT NULL DEFAULT 'starter';
ALTER TABLE users ADD COLUMN IF NOT EXISTS run_limit       double precision;           -- USD per run; NULL = the plan's default
DO $$ BEGIN
    ALTER TABLE users ADD CONSTRAINT users_plan_check CHECK (plan IN ('starter', 'pro', 'agency'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
