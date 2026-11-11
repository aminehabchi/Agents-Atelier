-- Team collaboration and share links (see collab.py and share.py).

-- The people a project's owner invited: editors work on it like the owner (on the owner's Claude key),
-- viewers watch the studio and comment. The owner stays in projects.owner_id.
CREATE TABLE IF NOT EXISTS project_members (
    project_id uuid NOT NULL,
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role       text NOT NULL CHECK (role IN ('editor', 'viewer')),
    invited_by uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at double precision NOT NULL,
    PRIMARY KEY (project_id, user_id)
);
CREATE INDEX IF NOT EXISTS project_members_user ON project_members(user_id);

-- Invitations to an email address that has no confirmed account yet: they become memberships when it confirms.
CREATE TABLE IF NOT EXISTS project_invites (
    project_id uuid NOT NULL,
    email      text NOT NULL,
    role       text NOT NULL CHECK (role IN ('editor', 'viewer')),
    invited_by uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at double precision NOT NULL,
    PRIMARY KEY (project_id, email)
);
CREATE INDEX IF NOT EXISTS project_invites_email ON project_invites(email);

-- Read-only links to a project's site for people without an account. The token is the link's secret (like a
-- project's unguessable preview address); it is kept so the team can copy the link again.
CREATE TABLE IF NOT EXISTS share_links (
    id          uuid PRIMARY KEY,
    project_id  uuid NOT NULL,
    token       text NOT NULL UNIQUE,
    label       text NOT NULL DEFAULT '',
    comments    boolean NOT NULL DEFAULT true,       -- visitors may pin comments on the page
    created_by  uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at  double precision NOT NULL,
    expires_at  double precision,                    -- NULL: never
    revoked_at  double precision,
    views       integer NOT NULL DEFAULT 0,
    last_viewed double precision
);
CREATE INDEX IF NOT EXISTS share_links_project ON share_links(project_id);

-- Comments pinned to a spot of a page, left through a share link (or by the team in the studio).
CREATE TABLE IF NOT EXISTS share_comments (
    id          uuid PRIMARY KEY,
    project_id  uuid NOT NULL,
    link_id     uuid REFERENCES share_links(id) ON DELETE SET NULL,
    page        text NOT NULL,                       -- index.html, about.html…
    selector    text NOT NULL DEFAULT '',            -- the element the pin sits on
    x           double precision NOT NULL DEFAULT 0, -- where in that element, 0..1
    y           double precision NOT NULL DEFAULT 0,
    doc_x       double precision NOT NULL DEFAULT 0, -- fallback: where in the page, x as 0..1 of its width, y in px
    doc_y       double precision NOT NULL DEFAULT 0,
    viewport    integer NOT NULL DEFAULT 0,          -- the visitor's window width
    snippet     text NOT NULL DEFAULT '',            -- the start of that element's text, to recognise it
    author      text NOT NULL DEFAULT '',
    text        text NOT NULL,
    created_at  double precision NOT NULL,
    resolved_at double precision,
    resolved_by uuid REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS share_comments_project ON share_comments(project_id, created_at);
