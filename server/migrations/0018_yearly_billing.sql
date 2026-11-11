-- Paying for a plan by the year (see billing.py): the period of each subscription, and of the member's current plan.
ALTER TABLE billing_subscriptions ADD COLUMN IF NOT EXISTS period text NOT NULL DEFAULT 'monthly';   -- monthly · yearly
ALTER TABLE users ADD COLUMN IF NOT EXISTS billing_period text NOT NULL DEFAULT 'monthly';
