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

Nothing here touches the project's files: a link always shows the current site.
"""

import re
import secrets
import time
import uuid

import db

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
           "expires": r["expires_at"], "views": r["views"], "last_viewed": r["last_viewed"], "state": state}
    if with_token:
        out["token"] = r["token"]
    return out


def _comment(r) -> dict:
    return {"id": str(r["id"]), "link": str(r["link_id"]) if r["link_id"] else None, "page": r["page"], "selector": r["selector"],
            "x": r["x"], "y": r["y"], "doc_x": r["doc_x"], "doc_y": r["doc_y"], "viewport": r["viewport"], "snippet": r["snippet"],
            "author": r["author"], "text": r["text"], "created": r["created_at"], "resolved": r["resolved_at"]}


# ------------------------------------------------------------ the team's side

async def links(pid: str) -> list[dict]:
    rows = await db.pool.fetch("SELECT * FROM share_links WHERE project_id = $1 ORDER BY created_at DESC", uuid.UUID(pid))
    counts = {str(r["link_id"]): r["n"] for r in await db.pool.fetch(
        "SELECT link_id, count(*) AS n FROM share_comments WHERE project_id = $1 AND link_id IS NOT NULL GROUP BY link_id", uuid.UUID(pid))}
    return [{**_link(r), "comment_count": counts.get(str(r["id"]), 0)} for r in rows]


async def create(pid: str, actor: dict, body: dict) -> dict:
    days = body.get("days")
    if days in (None, 0, "0", "never"):
        expires = None
    else:
        try:
            days = int(days)
        except (TypeError, ValueError):
            raise ShareError("Pick when the link expires.")
        if days not in EXPIRY_DAYS:
            raise ShareError(f"A link lasts {', '.join(map(str, EXPIRY_DAYS))} days, or never expires.")
        expires = time.time() + days * 86400
    key = uuid.UUID(pid)
    active = await db.pool.fetchval("""SELECT count(*) FROM share_links WHERE project_id = $1 AND revoked_at IS NULL
                                       AND (expires_at IS NULL OR expires_at > $2)""", key, time.time())
    if active >= MAX_LINKS:
        raise ShareError(f"A project can have up to {MAX_LINKS} active links. Revoke one first.")
    row = await db.pool.fetchrow("""INSERT INTO share_links (id, project_id, token, label, comments, created_by, created_at, expires_at)
                                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING *""",
                                 uuid.uuid4(), key, secrets.token_urlsafe(24), _text(body.get("label"), 80),
                                 body.get("comments") is not False, actor["id"], time.time(), expires)
    return {**_link(row), "comment_count": 0}


async def revoke(pid: str, link_id: str):
    row = await db.pool.fetchrow("UPDATE share_links SET revoked_at = coalesce(revoked_at, $3) WHERE project_id = $1 AND id = $2 RETURNING token",
                                 uuid.UUID(pid), _id(link_id), time.time())
    if not row:
        raise ShareError("This link doesn't exist.", 404)
    _cache.pop(row["token"], None)


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
