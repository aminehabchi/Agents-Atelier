"""Share links: a read-only view of a project's site for people without an account (a client, a colleague), with an
expiry date, and comments they pin to a spot of a page.

  /s/<token>                    the share page (static/share.html): the site in a sandboxed frame, a page picker, the
                                device sizes and the comments
  /s/<token>/site/<path>        the site's files, served sandboxed like /work/<pid>/; web pages get a small script
                                (static/share-pin.js) that places the pins and reports where a visitor clicks
  /s/<token>/info               the project's name, pages and whether comments are on
  /s/<token>/comments           GET the comments left through this link, POST a new one

The token is the only secret: whoever has the link sees the site until it expires or the team revokes it. The team
(owner and editors) creates and revokes links and reads, resolves or deletes every comment in the studio's Share tab;
viewers read them.

A link shows either the live site, or one saved version ("frozen"): a version the team picks, or one saved when the
link is made. A frozen link never shows work in progress, and its version is kept for as long as the link isn't revoked
(versions.py asks `pinned`), even past the plan's number of versions.
"""

import re
import secrets
import time
import uuid

from core import db

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
MAX_LINKS = 20                 # active links per project
MAX_COMMENTS = 300             # comments per link
TEXT_LIMIT, NAME_LIMIT, SELECTOR_LIMIT, SNIPPET_LIMIT = 2000, 60, 400, 120
EXPIRY_DAYS = (1, 7, 30, 90)   # the choices; 0 or None = never
PAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,120}\.html?$")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_cache: dict[str, tuple[float, dict | None]] = {}   # token -> (when, link): a page loads many files
CACHE_SECONDS = 10


class ShareError(Exception):
    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.status = status


def _text(value, limit: int, *, multiline: bool = False) -> str:
    text = CONTROL.sub("", str(value or ""))
    if not multiline:
        text = " ".join(text.split())
    return text.strip()[:limit]


def _num(value, low: float, high: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return low
    return min(max(n, low), high) if n == n else low      # n != n: NaN


def _link(r, with_token: bool = True) -> dict:
    now = time.time()
    state = ("revoked" if r["revoked_at"] else "expired" if r["expires_at"] and r["expires_at"] <= now else "active")
    out = {"id": str(r["id"]), "label": r["label"], "comments": r["comments"], "created": r["created_at"],
           "expires": r["expires_at"], "views": r["views"], "last_viewed": r["last_viewed"], "state": state,
           "version": str(r["version_id"]) if r["version_id"] else None, "version_n": r["version_n"]}
    if with_token:
        out["token"] = r["token"]
    return out


def _comment(r) -> dict:
    return {"id": str(r["id"]), "link": str(r["link_id"]) if r["link_id"] else None, "page": r["page"], "selector": r["selector"],
            "x": r["x"], "y": r["y"], "doc_x": r["doc_x"], "doc_y": r["doc_y"], "viewport": r["viewport"], "snippet": r["snippet"],
            "author": r["author"], "text": r["text"], "created": r["created_at"], "resolved": r["resolved_at"],
            "reply": r["reply"] if "reply" in r.keys() else "", "reply_at": r["reply_at"] if "reply_at" in r.keys() else None}


# ------------------------------------------------------------ the team's side

async def links(pid: str) -> list[dict]:
    rows = await db.pool.fetch("SELECT * FROM share_links WHERE project_id = $1 ORDER BY created_at DESC", uuid.UUID(pid))
    counts = {str(r["link_id"]): r["n"] for r in await db.pool.fetch(
        "SELECT link_id, count(*) AS n FROM share_comments WHERE project_id = $1 AND link_id IS NOT NULL GROUP BY link_id", uuid.UUID(pid))}
    return [{**_link(r), "comment_count": counts.get(str(r["id"]), 0)} for r in rows]


# versions a share link shows: never pruned while the link isn't revoked. {project id: {version id}}
pinned: dict[str, set[str]] = {}


async def load_pins():
    pinned.clear()
    for r in await db.pool.fetch("SELECT project_id, version_id FROM share_links WHERE version_id IS NOT NULL AND revoked_at IS NULL"):
        pinned.setdefault(str(r["project_id"]), set()).add(str(r["version_id"]))


async def create(pid: str, actor: dict, body: dict, version: dict | None = None) -> dict:
    """A new link. `version` ({id, n}, chosen by main.py) freezes it on that saved version; None shows the live site."""
    days = body.get("days")
    if days in (None, 0, "0", "never"):
        expires = None
    else:
        try:
            days = int(days)
        except (TypeError, ValueError, OverflowError):
            raise ShareError("Pick when the link expires.")
        if days not in EXPIRY_DAYS:
            raise ShareError(f"A link lasts {', '.join(map(str, EXPIRY_DAYS))} days, or never expires.")
        expires = time.time() + days * 86400
    key = uuid.UUID(pid)
    active = await db.pool.fetchval("""SELECT count(*) FROM share_links WHERE project_id = $1 AND revoked_at IS NULL
                                       AND (expires_at IS NULL OR expires_at > $2)""", key, time.time())
    if active >= MAX_LINKS:
        raise ShareError(f"A project can have up to {MAX_LINKS} active links. Revoke one first.")
    row = await db.pool.fetchrow("""INSERT INTO share_links (id, project_id, token, label, comments, created_by, created_at, expires_at,
                                                             version_id, version_n)
                                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING *""",
                                 uuid.uuid4(), key, secrets.token_urlsafe(24), _text(body.get("label"), 80),
                                 body.get("comments") is not False, actor["id"], time.time(), expires,
                                 uuid.UUID(version["id"]) if version else None, version["n"] if version else None)
    if version:
        pinned.setdefault(pid, set()).add(version["id"])
    return {**_link(row), "comment_count": 0}


async def revoke(pid: str, link_id: str):
    row = await db.pool.fetchrow("""UPDATE share_links SET revoked_at = coalesce(revoked_at, $3) WHERE project_id = $1 AND id = $2
                                    RETURNING token, version_id""", uuid.UUID(pid), _id(link_id), time.time())
    if not row:
        raise ShareError("This link doesn't exist.", 404)
    _cache.pop(row["token"], None)
    await load_pins()                           # its version may be pruned again (unless another link shows it)


async def comments(pid: str) -> list[dict]:
    rows = await db.pool.fetch("SELECT * FROM share_comments WHERE project_id = $1 ORDER BY created_at", uuid.UUID(pid))
    return [_comment(r) for r in rows]


async def resolve(pid: str, comment_id: str, actor: dict, done: bool = True) -> dict:
    row = await db.pool.fetchrow("""UPDATE share_comments SET resolved_at = $3, resolved_by = $4
                                    WHERE project_id = $1 AND id = $2 RETURNING *""",
                                 uuid.UUID(pid), _id(comment_id), time.time() if done else None, actor["id"] if done else None)
    if not row:
        raise ShareError("This comment doesn't exist.", 404)
    return _comment(row)


async def reply(pid: str, comment_id: str, actor: dict, text) -> dict:
    """The team's answer to a comment (one per comment; an empty text removes it). The visitor sees it on the shared page."""
    text = _text(text, TEXT_LIMIT, multiline=True)
    row = await db.pool.fetchrow("""UPDATE share_comments SET reply = $3, reply_at = $4, reply_by = $5
                                    WHERE project_id = $1 AND id = $2 RETURNING *""",
                                 uuid.UUID(pid), _id(comment_id), text, time.time() if text else None, actor["id"] if text else None)
    if not row:
        raise ShareError("This comment doesn't exist.", 404)
    return _comment(row)


async def update_link(pid: str, link_id: str, body: dict) -> dict:
    """Change a link after it's made: its name, when it expires (days from now, or 0 for never), whether visitors comment."""
    sets, args = [], [uuid.UUID(pid), _id(link_id)]
    if "label" in body:
        args.append(_text(body.get("label"), 80)); sets.append(f"label = ${len(args)}")
    if "comments" in body:
        if not isinstance(body["comments"], bool):
            raise ShareError("“comments” must be true or false.")
        args.append(body["comments"]); sets.append(f"comments = ${len(args)}")
    if "days" in body:
        days = body["days"]
        if days in (0, "0", None, "never"):
            expires = None
        else:
            try:
                days = int(days)
            except (TypeError, ValueError, OverflowError):
                raise ShareError("Pick when the link expires.")
            if days not in EXPIRY_DAYS:
                raise ShareError(f"A link lasts {', '.join(map(str, EXPIRY_DAYS))} days, or never expires.")
            expires = time.time() + days * 86400
        args.append(expires); sets.append(f"expires_at = ${len(args)}")
    if not sets:
        raise ShareError("Nothing to change.")
    row = await db.pool.fetchrow(f"""UPDATE share_links SET {", ".join(sets)} WHERE project_id = $1 AND id = $2 AND revoked_at IS NULL
                                     RETURNING *""", *args)
    if not row:
        raise ShareError("This link doesn't exist or was turned off.", 404)
    _cache.pop(row["token"], None)
    count = await db.pool.fetchval("SELECT count(*) FROM share_comments WHERE link_id = $1", row["id"])
    return {**_link(row), "comment_count": count}


async def delete_comment(pid: str, comment_id: str):
    await db.pool.execute("DELETE FROM share_comments WHERE project_id = $1 AND id = $2", uuid.UUID(pid), _id(comment_id))


# ------------------------------------------------------------ the visitor's side

async def open_link(token: str) -> dict | None:
    """The link behind a token, if it still works (not revoked, not expired), else None."""
    if not TOKEN_RE.match(token or ""):
        return None
    now = time.time()
    hit = _cache.get(token)
    if hit and now - hit[0] < CACHE_SECONDS:
        link = hit[1]
    else:
        row = await db.pool.fetchrow("SELECT * FROM share_links WHERE token = $1", token)
        link = {**_link(row), "project": str(row["project_id"])} if row else None
        if len(_cache) > 2000:
            _cache.clear()
        _cache[token] = (now, link)
    if not link or link["state"] != "active" or (link["expires"] and link["expires"] <= now):
        return None
    return link


async def viewed(link: dict):
    await db.pool.execute("UPDATE share_links SET views = views + 1, last_viewed = $2 WHERE id = $1", uuid.UUID(link["id"]), time.time())


async def link_comments(link: dict) -> list[dict]:
    rows = await db.pool.fetch("SELECT * FROM share_comments WHERE link_id = $1 ORDER BY created_at", uuid.UUID(link["id"]))
    return [_comment(r) for r in rows]


async def add_comment(link: dict, body: dict, pages: list[str]) -> dict:
    if not link["comments"]:
        raise ShareError("Comments are turned off for this link.", 403)
    text = _text(body.get("text"), TEXT_LIMIT, multiline=True)
    if not text:
        raise ShareError("Write a comment first.")
    page = _text(body.get("page"), 130)
    if not PAGE_RE.match(page) or ".." in page.split("/") or page not in pages:
        page = pages[0] if pages else "index.html"
    count = await db.pool.fetchval("SELECT count(*) FROM share_comments WHERE link_id = $1", uuid.UUID(link["id"]))
    if count >= MAX_COMMENTS:
        raise ShareError("This link has reached its limit of comments. Ask the team for a new link.", 429)
    row = await db.pool.fetchrow(
        """INSERT INTO share_comments (id, project_id, link_id, page, selector, x, y, doc_x, doc_y, viewport, snippet, author, text, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14) RETURNING *""",
        uuid.uuid4(), uuid.UUID(link["project"]), uuid.UUID(link["id"]), page, _text(body.get("selector"), SELECTOR_LIMIT),
        _num(body.get("x"), 0, 1), _num(body.get("y"), 0, 1), _num(body.get("doc_x"), 0, 1), _num(body.get("doc_y"), 0, 200000),
        int(_num(body.get("viewport"), 0, 10000)), _text(body.get("snippet"), SNIPPET_LIMIT),
        _text(body.get("author"), NAME_LIMIT) or "Guest", text, time.time())
    return _comment(row)


def _id(value) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ShareError("Not found.", 404)
