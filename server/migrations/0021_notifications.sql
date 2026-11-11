-- Emails the app sends on its own (see notify.py).
ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_runs boolean NOT NULL DEFAULT true;          -- email me when a long run finishes
ALTER TABLE billing_subscriptions ADD COLUMN IF NOT EXISTS reminded_for double precision;      -- the renewal date already reminded of
