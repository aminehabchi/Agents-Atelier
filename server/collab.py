"""Team collaboration: a project's owner invites people to work on it with them.

Roles on a project
  owner    the account that created it (projects.owner_id): everything, including inviting people and deleting it
  editor   works on it like the owner: prompts, Continue, retry, code, versions, brand kit, share links. Runs use the
           owner's Claude key and spending limit, so an editor spends the owner's money. Can't invite or delete.
  viewer   opens the studio and watches: chat, preview, code, versions, stats, comments. Changes nothing.
  admin    (the account role) can open every project and acts as its owner.

Inviting needs a plan with `members` (Agency). When the owner's plan stops including it, the people they invited lose
access until the owner moves back: the memberships are kept, just not honoured (see team_on).

An invitation to an email without a confirmed account waits in project_invites and becomes a membership when that
email confirms its account (claim_invites).

Who belongs to which project is kept in memory (loaded at startup, changed through this module) because access checks
run on every request and are synchronous; Postgres is the source of truth.
"""

import time
import uuid

import auth
import db
import plans

ROLES = ("editor", "viewer")
EDIT = {"owner", "admin", "editor"}

members: dict[str, dict[str, str]] = {}   # project id -> {user id: role}
_team_owners: set[str] = set()             # users whose plan includes members (Agency) or admins


class CollabError(Exception):
    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.status = status


async def load():
    members.clear()
    _team_owners.clear()
    for r in await db.pool.fetch("SELECT project_id, user_id, role FROM project_members"):
        members.setdefault(str(r["project_id"]), {})[str(r["user_id"])] = r["role"]
    for r in await db.pool.fetch("SELECT id, role, plan FROM users"):
        note_user(dict(r))


def note_user(user: dict | None):
    """Keep the list of owners whose invitations count up to date (after a plan or role change)."""
    if not user or not user.get("id"):
        return
    uid = str(user["id"])
    if user.get("role") == "admin" or plans.of(user).get("members"):
        _team_owners.add(uid)
    else:
        _team_owners.discard(uid)


async def refresh_user(user_id):
    row = await db.pool.fetchrow("SELECT id, role, plan FROM users WHERE id = $1", uuid.UUID(str(user_id)))
    if row:
        note_user(dict(row))


def team_on(owner_id: str | None) -> bool:
    return bool(owner_id) and owner_id in _team_owners


def role_of(user: dict | None, meta: dict) -> str | None:
    """The user's role on this project, or None when they can't open it."""
    if not user or user.get("role") == "unconfirmed":
        return None
    uid = str(user["id"])
    if meta.get("owner") == uid:
        return "owner"
    if user.get("role") == "admin":
        return "admin"
    role = members.get(meta["id"], {}).get(uid)
    return role if role and team_on(meta.get("owner")) else None


def can_edit(user: dict | None, meta: dict) -> bool:
    return role_of(user, meta) in EDIT


def can_manage(user: dict | None, meta: dict) -> bool:
    """Invite and remove people, delete the project: its owner (or an admin)."""
    return role_of(user, meta) in ("owner", "admin")


def forget_project(pid: str):
    members.pop(pid, None)


def _clean_role(role) -> str:
    if role not in ROLES:
        raise CollabError("A role is editor or viewer.")
    return role


async def list_people(meta: dict) -> dict:
    """The owner, the members and the waiting invitations, for the studio's Share tab."""
    pid = uuid.UUID(meta["id"])
    owner = None
    if meta.get("owner"):
        r = await db.pool.fetchrow("SELECT id, email, name FROM users WHERE id = $1", uuid.UUID(meta["owner"]))
        owner = {"id": str(r["id"]), "email": r["email"], "name": r["name"]} if r else None
    rows = await db.pool.fetch("""SELECT m.user_id, m.role, m.created_at, u.email, u.name FROM project_members m
                                  JOIN users u ON u.id = m.user_id WHERE m.project_id = $1 ORDER BY m.created_at""", pid)
    invites = await db.pool.fetch("SELECT email, role, created_at FROM project_invites WHERE project_id = $1 ORDER BY created_at", pid)
    return {"owner": owner, "active": team_on(meta.get("owner")),
            "members": [{"id": str(r["user_id"]), "email": r["email"], "name": r["name"], "role": r["role"], "since": r["created_at"]}
                        for r in rows],
            "invites": [{"email": r["email"], "role": r["role"], "since": r["created_at"]} for r in invites]}


MAX_PEOPLE = 20   # members + waiting invitations per project


async def invite(meta: dict, actor: dict, email, role) -> dict:
    """Add someone by email. An existing confirmed account joins at once; any other email gets a waiting invitation."""
    owner = await db.pool.fetchrow("SELECT id, role, plan FROM users WHERE id = $1", uuid.UUID(meta["owner"])) if meta.get("owner") else None
    if not owner:
        raise CollabError("This older project has no owner. Duplicate it into your account first.")
    if not (owner["role"] == "admin" or plans.of(dict(owner)).get("members")):
        raise plans.PlanError("Working on a project with other people comes with the Agency plan.", "agency")
    role = _clean_role(role)
    email = auth.clean_email(email)
    pid = uuid.UUID(meta["id"])
    count = await db.pool.fetchval("""SELECT (SELECT count(*) FROM project_members WHERE project_id = $1)
                                           + (SELECT count(*) FROM project_invites WHERE project_id = $1)""", pid)
    if count >= MAX_PEOPLE:
        raise CollabError(f"A project can have up to {MAX_PEOPLE} people besides its owner.")
    user = await db.pool.fetchrow("SELECT id, email, role, language FROM users WHERE email = $1", email)
    if user and str(user["id"]) == meta["owner"]:
        raise CollabError("That's the project's owner.")
    now = time.time()
    if user and user["role"] != "unconfirmed":
        exists = await db.pool.fetchval("SELECT role FROM project_members WHERE project_id = $1 AND user_id = $2", pid, user["id"])
        if exists:
            raise CollabError(f"{email} is already on this project.")
        await db.pool.execute("""INSERT INTO project_members (project_id, user_id, role, invited_by, created_at)
                                 VALUES ($1, $2, $3, $4, $5)""", pid, user["id"], role, actor["id"], now)
        members.setdefault(meta["id"], {})[str(user["id"])] = role
        auth.send_later(email, *auth.email_text("invited", user["language"]))
        return {"joined": True, "email": email, "role": role}
    await db.pool.execute("""INSERT INTO project_invites (project_id, email, role, invited_by, created_at) VALUES ($1, $2, $3, $4, $5)
                             ON CONFLICT (project_id, email) DO UPDATE SET role = $3""", pid, email, role, actor["id"], now)
    auth.send_later(email, *auth.email_text("invited_signup", user["language"] if user else actor.get("language") or "en"))
    return {"joined": False, "email": email, "role": role}


async def set_role(meta: dict, user_id: str, role) -> dict:
    role = _clean_role(role)
    uid = _uid(user_id)
    done = await db.pool.fetchval("UPDATE project_members SET role = $3 WHERE project_id = $1 AND user_id = $2 RETURNING role",
                                  uuid.UUID(meta["id"]), uid, role)
    if not done:
        raise CollabError("This person isn't on the project.", 404)
    members.setdefault(meta["id"], {})[str(uid)] = role
    return {"ok": True}


async def remove(meta: dict, user_id: str):
    uid = _uid(user_id)
    await db.pool.execute("DELETE FROM project_members WHERE project_id = $1 AND user_id = $2", uuid.UUID(meta["id"]), uid)
    members.get(meta["id"], {}).pop(str(uid), None)


async def cancel_invite(meta: dict, email):
    email = str(email or "").strip().lower()
    await db.pool.execute("DELETE FROM project_invites WHERE project_id = $1 AND email = $2", uuid.UUID(meta["id"]), email)


async def claim_invites(user: dict) -> int:
    """A newly confirmed account: the invitations waiting for its email become memberships."""
    async with db.pool.acquire() as con:
        async with con.transaction():
            rows = await con.fetch("DELETE FROM project_invites WHERE email = $1 RETURNING project_id, role, invited_by, created_at",
                                   user["email"])
            for r in rows:
                await con.execute("""INSERT INTO project_members (project_id, user_id, role, invited_by, created_at)
                                     VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING""",
                                  r["project_id"], user["id"], r["role"], r["invited_by"], time.time())
    import notify                              # (not at the top: notify imports auth, like this module)
    for r in rows:
        members.setdefault(str(r["project_id"]), {}).setdefault(str(user["id"]), r["role"])
        if r["invited_by"]:
            await notify.joined(str(r["project_id"]), r["invited_by"], user["email"], r["role"])
    return len(rows)


def _uid(user_id) -> uuid.UUID:
    try:
        return uuid.UUID(str(user_id))
    except ValueError:
        raise CollabError("This person isn't on the project.", 404)
