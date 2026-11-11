-- A member asks to move to a higher plan from their profile; an admin approves it (payment isn't online yet).
ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_request    text NOT NULL DEFAULT '';   -- '' or the plan asked for
ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_request_at double precision;
