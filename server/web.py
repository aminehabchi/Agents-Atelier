"""HTTP helpers shared by the app and its routes: error pages, the host and origin checks, who may open what
(_check_access, can_access, current_user, require_admin), rate limits per request (_limited) and paging (page_of)."""

import errno
import re
import urllib.parse
import uuid
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from core import config
from core import db
from core import limits
from accounts import auth
from accounts import collab

from state import store


STATIC = Path(__file__).resolve().parent / "static"


# ---------------------------------------------------------------- errors: always a clear JSON message for the API

def _wants_json(request: Request) -> bool:
    return request.url.path.startswith("/api/") or "application/json" in request.headers.get("accept", "")


def _error_page(status: int, title: str, text: str) -> HTMLResponse:
    return HTMLResponse(status_code=status, content=f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title} · Agents Atelier</title>
<link rel="icon" href="/static/logo.svg" type="image/svg+xml"><link rel="stylesheet" href="/static/base.css"></head>
<body style="min-height:100vh;display:grid;place-items:center;padding:24px;text-align:center">
<div><p style="color:var(--stone);margin:0 0 8px">Error {status}</p>
<h1 style="font-weight:400;font-size:44px;letter-spacing:-.05em;margin:0 0 12px">{title}</h1>
<p style="color:var(--graphite);max-width:420px;margin:0 auto 28px">{text}</p>
<a class="btn btn-dark" href="/projects">Go to your projects</a> <a class="btn btn-ghost" href="/">Home</a></div></body></html>""")


def explain_exception(exc: BaseException, ref: str) -> tuple[int, str, dict]:
    """(status, message, headers) for an exception no route turned into an HTTPException.
    Postgres down or too slow → 503; a value Postgres refuses → 400; a full disk or a file error → 500 with what
    happened; anything else is a bug → 500 with the ref of the traceback in the server log."""
    if db.unavailable(exc):
        return 503, db.UNAVAILABLE, {"Retry-After": "30"}
    if db.bad_value(exc):
        return 400, db.BAD_VALUE, {}
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return 507, "The disk is full, so the server couldn't save. Free some space and try again.", {}
    if isinstance(exc, OSError) and not isinstance(exc, (ConnectionError, TimeoutError)):
        return 500, f"The server couldn't read or write a file ({exc.strerror or 'input/output error'}). [ref {ref}]", {}
    return 500, f"Something went wrong on the server. Details are in the server log under ref {ref}.", {}


# ---------------------------------------------------------------- http

ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]"} | config.ALLOWED_HOSTS


def _host_ok(host: str) -> bool:
    host = (host or "").lower()
    name = host.split("]")[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]
    return name in ALLOWED_HOSTS


def _origin_ok(request_host: str, origin: str | None) -> bool:
    if not origin:                               # same-origin GETs and tools like curl send no Origin
        return True
    return origin.split("://", 1)[-1].lower() == (request_host or "").lower()


# ---- who may do what. Everything is private unless listed here.
PUBLIC_PAGES = {"/", "/login", "/register", "/forgot", "/confirm", "/terms", "/privacy", "/favicon.ico",
                "/api/billing/paypal/webhook"}     # PayPal calls it; billing.py checks its signature
PROJECT_PATH = re.compile(r"^/(?:api/projects|p|media)/([^/]+)")


def is_public(path: str, method: str = "GET") -> bool:
    if path == "/api/agents" and method == "GET":   # names and descriptions for the landing page; list_agents checks the rest
        return True
    # /work/<uuid>/… is reached by its unguessable project id: generated pages run in a sandbox with no origin,
    # so browsers don't send the session cookie for their CSS, scripts and images.
    return path in PUBLIC_PAGES or path.startswith(("/static/", "/work/", "/api/auth/", "/s/"))


def can_access(user: dict | None, meta: dict) -> bool:
    """A project belongs to its owner and the people they invited (collab.py); admins can open every project
    (projects with no owner are admins' only)."""
    return collab.role_of(user, meta) is not None


# API calls the general rate limit leaves alone: PayPal's webhooks (they come from PayPal, retried by it).
RATE_FREE = {"/api/billing/paypal/webhook"}

# Changes a viewer may still make: leaving the project.
VIEWER_WRITES = re.compile(r"^/api/projects/[^/]+/membership$")


def _project_of_path(path: str) -> dict | None:
    """The project a URL points at, or None when it points at none (the route then answers by itself)."""
    m = PROJECT_PATH.match(path)
    if not m:
        return None
    try:
        return store.get(str(uuid.UUID(m.group(1))))
    except ValueError:
        return None


def _denied(request, status: int, detail: str, page: str):
    if request.scope["path"].startswith("/api/"):
        return JSONResponse({"detail": detail}, status_code=status)
    return RedirectResponse(page, status_code=303)


async def _check_access(request):
    """Returns a response when this request isn't allowed, else None. Sets request.state.user."""
    path = request.scope["path"]
    request.state.user = None
    if path.startswith(("/work/.projects", "/work/.brand")):     # project data, chat logs and brand kits are never served as files
        return JSONResponse({"detail": "This page or file doesn't exist."}, status_code=404)
    if path.startswith(("/static/", "/work/", "/s/")):
        return None                              # share links check their token themselves
    user = request.state.user = await auth.user_for_token(request.cookies.get(auth.COOKIE))
    if is_public(path, request.method):
        return None
    if not user:
        next_url = urllib.parse.quote(path + (f"?{request.url.query}" if request.url.query else ""), safe="")
        return _denied(request, 401, "Log in first.", f"/login?next={next_url}")
    if user["role"] == "unconfirmed":
        return _denied(request, 403, "Confirm your email first.", "/confirm")
    meta = _project_of_path(path)
    if meta is not None and not can_access(user, meta):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "This project doesn't exist (it may have been deleted)."}, status_code=404)
        return _error_page(404, "Not found", "This project doesn't exist (it may have been deleted).")
    if (meta is not None and request.method not in ("GET", "HEAD", "OPTIONS") and not collab.can_edit(user, meta)
            and not VIEWER_WRITES.match(path)):
        return JSONResponse({"detail": "You can view this project but not change it. Ask its owner to make you an editor."},
                            status_code=403)
    return None


def current_user(request: Request) -> dict:
    return request.state.user


def require_admin(request: Request) -> dict:
    user = request.state.user
    if not user or user["role"] != "admin":
        raise HTTPException(403, "Only an admin can do this.")
    return user


def _limited(limit: limits.Limit, request: Request, what: str, per_member: bool = False):
    """Refuse with 429 past `limit`: per address (sign-up, login…), or per member when logged in (per_member)."""
    wait = limit.wait(limits.key_for(request) if per_member else limits.client_ip(request))
    if wait:
        raise HTTPException(429, (f"You've made a lot of {what} recently. Try again in {limits.wait_text(wait)}." if per_member else
                                  f"Too many {what} from your network. Try again in {limits.wait_text(wait)}."),
                            headers={"Retry-After": str(wait)})


def page_of(items: list, page: int, per_page: int, limit: int = 100) -> dict:
    """One page of `items`, with the totals the shared pager needs (page is clamped to the last page)."""
    per_page = max(1, min(per_page, limit))
    total = len(items)
    pages = max(1, -(-total // per_page))
    page = max(1, min(page, pages))
    start = (page - 1) * per_page
    return {"items": items[start:start + per_page], "page": page, "pages": pages, "per_page": per_page, "total": total}
