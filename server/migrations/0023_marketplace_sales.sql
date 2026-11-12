-- Paid agents and teams in the marketplace (see accounts/sales.py).
--   A creator on the Agency plan sets a price when publishing. A buyer pays once with PayPal; the creator earns 70%,
--   the platform keeps 30%. Earnings build up as a balance the creator asks to be paid out; an admin sends it.
ALTER TABLE agents ADD COLUMN IF NOT EXISTS price  numeric(10, 2) NOT NULL DEFAULT 0;    -- USD, 0 = free
ALTER TABLE teams  ADD COLUMN IF NOT EXISTS price  numeric(10, 2) NOT NULL DEFAULT 0;
-- a copy of a paid agent: it runs, but its owner can't read, edit, export, duplicate or publish its instructions
ALTER TABLE agents ADD COLUMN IF NOT EXISTS locked boolean NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS market_purchases (
    id             text PRIMARY KEY,                                  -- the PayPal order id
    buyer_id       uuid REFERENCES users(id) ON DELETE SET NULL,
    seller_id      uuid REFERENCES users(id) ON DELETE SET NULL,
    kind           text NOT NULL CHECK (kind IN ('agent', 'team')),
    item_pk        bigint NOT NULL,                                   -- agents.pk or teams.pk (kept if it's deleted)
    item_name      text NOT NULL DEFAULT '',
    amount         numeric(10, 2) NOT NULL,                           -- what the buyer paid
    seller_share   numeric(10, 2) NOT NULL,                           -- 70%
    platform_share numeric(10, 2) NOT NULL,                           -- 30%
    currency       text NOT NULL DEFAULT 'USD',
    status         text NOT NULL DEFAULT 'created'                    -- created · completed · refunded
                   CHECK (status IN ('created', 'completed', 'refunded')),
    capture_id     text NOT NULL DEFAULT '',
    created        double precision NOT NULL,
    completed      double precision
);
CREATE INDEX IF NOT EXISTS market_purchases_buyer  ON market_purchases (buyer_id, kind, item_pk);
CREATE INDEX IF NOT EXISTS market_purchases_seller ON market_purchases (seller_id, status);

CREATE TABLE IF NOT EXISTS creator_payouts (
    id           bigserial PRIMARY KEY,
    user_id      uuid REFERENCES users(id) ON DELETE SET NULL,
    amount       numeric(10, 2) NOT NULL,
    paypal_email text NOT NULL,
    status       text NOT NULL DEFAULT 'requested' CHECK (status IN ('requested', 'paid', 'rejected')),
    reference    text NOT NULL DEFAULT '',                            -- the PayPal transaction id the admin sent it with
    note         text NOT NULL DEFAULT '',
    requested    double precision NOT NULL,
    decided      double precision
);
CREATE INDEX IF NOT EXISTS creator_payouts_user ON creator_payouts (user_id, status);

ALTER TABLE users ADD COLUMN IF NOT EXISTS payout_email text NOT NULL DEFAULT '';
