-- A share link can show one saved version instead of the live site (see share.py), so clients never see half-finished
-- work while agents run. NULL: the live site. That version is kept for as long as the link exists (versions.py).
ALTER TABLE share_links ADD COLUMN IF NOT EXISTS version_id uuid;
ALTER TABLE share_links ADD COLUMN IF NOT EXISTS version_n integer;
