-- Log in with Google or GitHub (see oauth.py): which provider account belongs to which member.
CREATE TABLE IF NOT EXISTS user_identities (
    provider   text NOT NULL,                        -- google · github
    subject    text NOT NULL,                        -- the provider's own, permanent user id
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email      text NOT NULL DEFAULT '',             -- the verified email it had when linked
    created_at double precision NOT NULL,
    PRIMARY KEY (provider, subject)
);
CREATE INDEX IF NOT EXISTS user_identities_user ON user_identities(user_id);
