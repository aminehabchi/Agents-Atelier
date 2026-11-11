-- The team answers a comment left through a share link; the visitor sees the answer on the shared page (share.py).
ALTER TABLE share_comments ADD COLUMN IF NOT EXISTS reply text NOT NULL DEFAULT '';
ALTER TABLE share_comments ADD COLUMN IF NOT EXISTS reply_at double precision;
ALTER TABLE share_comments ADD COLUMN IF NOT EXISTS reply_by uuid REFERENCES users(id) ON DELETE SET NULL;
