-- Deleting an account first deactivates it: it's removed for good after ACCOUNT_DELETE_DAYS (30) unless its owner
-- logs in again before then, which cancels the deletion.
ALTER TABLE users ADD COLUMN IF NOT EXISTS deactivated_at double precision;   -- NULL: active
ALTER TABLE users ADD COLUMN IF NOT EXISTS delete_after   double precision;   -- when the account and its data go
CREATE INDEX IF NOT EXISTS users_delete_after ON users(delete_after) WHERE delete_after IS NOT NULL;
