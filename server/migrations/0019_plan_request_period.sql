-- An upgrade request without online payment says whether the member wants to pay monthly or yearly (two months free).
ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_request_period text NOT NULL DEFAULT 'monthly';
