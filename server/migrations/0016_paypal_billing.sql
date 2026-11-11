-- Online payment with PayPal subscriptions (see billing.py).

-- One row per PayPal subscription a member started (a member may have had several: upgrades, re-subscriptions).
CREATE TABLE IF NOT EXISTS billing_subscriptions (
    id         text PRIMARY KEY,                     -- PayPal's subscription id (I-…)
    user_id    uuid REFERENCES users(id) ON DELETE SET NULL,
    provider   text NOT NULL DEFAULT 'paypal',
    plan       text NOT NULL,                        -- starter · pro · agency, as in plans.py
    status     text NOT NULL DEFAULT '',             -- PayPal's status: APPROVAL_PENDING, ACTIVE, SUSPENDED, CANCELLED, EXPIRED
    created    double precision NOT NULL,
    updated    double precision NOT NULL,
    next_billing double precision
);
CREATE INDEX IF NOT EXISTS billing_subscriptions_user ON billing_subscriptions(user_id);

-- The member's current subscription (empty: none; the plan was set by an admin or is free).
ALTER TABLE users ADD COLUMN IF NOT EXISTS billing_subscription text NOT NULL DEFAULT '';

-- Money actually received (or refunded), from PayPal's payment webhooks: what the revenue analytics call "received".
CREATE TABLE IF NOT EXISTS billing_payments (
    id              text PRIMARY KEY,                -- PayPal's sale / capture id
    subscription_id text,
    user_id         uuid REFERENCES users(id) ON DELETE SET NULL,
    provider        text NOT NULL DEFAULT 'paypal',
    amount          numeric(12, 2) NOT NULL,         -- negative for a refund
    currency        text NOT NULL,
    status          text NOT NULL,                   -- completed · refunded · reversed
    ts              double precision NOT NULL
);
CREATE INDEX IF NOT EXISTS billing_payments_ts ON billing_payments(ts);

-- Webhook events already handled (PayPal may send one several times).
CREATE TABLE IF NOT EXISTS billing_events (
    id   text PRIMARY KEY,
    type text NOT NULL,
    ts   double precision NOT NULL
);
