-- Every plan change, for the revenue analytics (monthly revenue over time, upgrades, downgrades).
CREATE TABLE IF NOT EXISTS plan_events (
    id        bigserial PRIMARY KEY,
    user_id   uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    from_plan text NOT NULL,
    to_plan   text NOT NULL,
    by_admin  boolean NOT NULL DEFAULT false,        -- an admin set it (approved a request), or the member moved down
    ts        double precision NOT NULL
);
CREATE INDEX IF NOT EXISTS plan_events_ts ON plan_events(ts);
-- members already on a paid plan before this table existed: count them from now
INSERT INTO plan_events (user_id, from_plan, to_plan, by_admin, ts)
SELECT id, 'starter', plan, true, extract(epoch FROM now()) FROM users
WHERE plan <> 'starter' AND NOT EXISTS (SELECT 1 FROM plan_events e WHERE e.user_id = users.id);
