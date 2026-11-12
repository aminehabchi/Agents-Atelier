"""Agents Atelier server.

Each project lives in work/<uuid>/ (the site the agents build).
Its metadata and chat log live in work/.projects/<uuid>.json and <uuid>.events.jsonl.

- GET    /                           landing page
- GET    /studio                     redirects to /projects (the studio opens only through a project)
- GET    /p/<uuid>                   the studio (chat + live preview) for one project
- GET    /projects                   all projects
- GET    /agents, /agents/new, /agents/<id>, /agents/<id>/edit, /agents/rules   agent pages
- GET    /teams, /teams/new, /teams/<id>, /teams/<id>/edit                      team pages
- GET    /guide                      how to use Agents Atelier
- GET    /login, /register, /confirm account pages · /users (admins)
- POST   /api/auth/register, /login, /confirm, /resend, /logout, /forgot, /reset · GET /api/auth/me, /options   accounts (see auth.py)
- PATCH  /api/auth/me, /preferences · POST /api/auth/password · GET,DELETE /api/auth/sessions · POST /api/auth/delete   your own account
- GET    /api/users · PATCH /api/users/<id> {role}   manage users (admins)
- GET    /work/<uuid>/...            the project's site, served live
- GET    /api/projects               list projects (newest activity first)
- POST   /api/projects               create a project -> {id}
- POST   /api/projects/import        create a project from existing files {name, description, zip | files} -> {id}
- GET    /api/projects/<id>          project info + saved chat events
- DELETE /api/projects/<id>          delete a project
- GET    /api/projects/<id>/files    files in the project, with content (Code tab)
- GET    /api/projects/<id>/download the project's site as a .zip (409 while agents are working)
- PUT    /api/projects/<id>/files    save one edited file {path, content}
- POST   /api/projects/<id>/uploads  upload a reference image {name, data: data-URL} -> {path, url}
- POST   /api/projects/<id>/assets   add a brand image to the site {name, data: data-URL, kind: logo|photo|image, note} -> {path, url}
- GET    /api/projects/<id>/versions               list code versions (newest first)
- POST   /api/projects/<id>/versions               save a version now {label}
- GET    /api/projects/<id>/versions/<n>/diff      changes from version n to the current code (or ?against=m)
- POST   /api/projects/<id>/versions/<n>/restore   make version n the current code (current is saved first)
- GET    /api/projects/<id>/versions/<n>/download  version n as a .zip
- GET    /api/stats?project=<id>     cost, time and tokens per agent (one project, or all)
- GET/POST/DELETE /api/presets       team presets (built-in + saved)
- GET/POST/PATCH/DELETE /api/projects/<id>/people[/<user>]   the people on a project (see collab.py)
- GET/POST/DELETE /api/projects/<id>/share[/<link>]          share links · GET/PATCH/DELETE …/comments[/<id>]  pinned comments
- GET    /s/<token>, /s/<token>/site/…, /s/<token>/info, GET/POST /s/<token>/comments   a share link (no account needed)
- WS     /ws/<id>                    send {"type":"prompt","text":...,"agents":[...],"images":[...]}, {"type":"stop"},
                                     {"type":"continue"}, {"type":"restart"} or {"type":"retry","agent":id,"note":...};
                                     receive live events

Run state is saved in the project's meta as "run": request, agents, done, notes, current agent and status
(running · stopped · error · interrupted · done), so a stopped run can be continued or started again.

This module only puts the app together: startup and shutdown (lifespan), the error handlers, the middleware and the
routers. The routes are in routes/ (one module per area), the agent-run pipeline in runs/, and the state they share
in state.py (see "Source files" in doc.md).
"""

import asyncio
import contextlib
import re
import secrets

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from core import config
from core import db
from core import legacy
from core import limits
from core import storage
from accounts import auth
from accounts import billing
from accounts import collab
from accounts import keys
from accounts import notify as notices
from accounts import plans
from accounts import share

from state import MAX_BODY, MAX_SITE_IMPORT, human_size, log, mark_interrupted_runs, store
from web import RATE_FREE, STATIC, _check_access, _error_page, _host_ok, _origin_ok, _wants_json, explain_exception
from routes.account import purge_loop
import routes.account
import routes.admin
import routes.agents
import routes.files
import routes.market
import routes.pages
import routes.projects
import routes.share
import routes.ws


@contextlib.asynccontextmanager
async def lifespan(_app):
    await db.init()
    await store.start(db.pool)
    await collab.load()
    await share.load_pins()
    imported = legacy.import_work_folder(config.LEGACY_WORK_DIR, store)     # once per project, from the old work/ folder
    if imported:
        log.info("Imported %d projects from %s into %s", imported, config.LEGACY_WORK_DIR, storage.ROOT)
    mark_interrupted_runs()
    purger = asyncio.create_task(purge_loop())
    reminders = asyncio.create_task(notices.loop())          # yearly renewal reminders
    yield
    purger.cancel()
    reminders.cancel()
    await store.stop()
    await db.close()


app = FastAPI(lifespan=lifespan)


# ---------------------------------------------------------------- errors: always a clear JSON message for the API


def _methods_for(path: str) -> list[str]:
    """Every method some route answers at this path (Starlette's own Allow header only lists the first route's)."""
    found = set()
    for route in app.routes:
        regex, methods = getattr(route, "path_regex", None), getattr(route, "methods", None)
        if regex is not None and methods and regex.match(path):
            found |= set(methods)
    return sorted(found - {"HEAD"})


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
    headers = getattr(exc, "headers", None)
    if exc.status_code == 404 and detail == "Not Found":
        detail = ("There's nothing at this address. Check the URL." if request.url.path.startswith("/api/")
                  else "This page or file doesn't exist.")
    elif exc.status_code == 405 and detail == "Method Not Allowed":
        allowed = _methods_for(request.url.path)
        detail = (f"This address doesn't accept {request.method} requests" + (f" (it accepts {', '.join(allowed)})." if allowed else "."))
        headers = {**(headers or {}), **({"Allow": ", ".join(allowed)} if allowed else {})}
    if _wants_json(request):
        return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=headers)
    return _error_page(exc.status_code, "Not found" if exc.status_code == 404 else "Something's not right", detail)


@app.exception_handler(auth.AuthError)
async def auth_error(request: Request, exc: auth.AuthError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


@app.exception_handler(collab.CollabError)
@app.exception_handler(share.ShareError)
@app.exception_handler(billing.BillingError)
async def collab_error(request: Request, exc: Exception):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


@app.exception_handler(plans.PlanError)
async def plan_error(request: Request, exc: plans.PlanError):
    """A feature the member's plan doesn't include: 403, with the plan that has it (the pages offer to upgrade)."""
    return JSONResponse({"detail": str(exc), "upgrade": exc.needs}, status_code=403)


def _field(loc) -> str:
    return " → ".join(str(x) for x in loc if x not in ("body", "query", "path", "header", "cookie"))


def validation_text(errors: list) -> str:
    """FastAPI's first validation error as one sentence a person can act on."""
    first = errors[0] if errors else {}
    kind, loc, where = first.get("type", ""), tuple(first.get("loc") or ()), _field(first.get("loc") or ())
    if kind == "json_invalid":
        return "The request body isn't valid JSON."
    if loc == ("body",) and kind == "missing":
        return "This request needs a JSON body."
    if loc == ("body",) and kind in ("dict_type", "model_attributes_type"):
        return "Send the details as a JSON object, like {\"name\": \"…\"}."
    if where and kind == "missing":
        return f"“{where}” is required."
    if where and kind in ("int_parsing", "int_type", "int_from_float"):
        return f"“{where}” must be a whole number."
    if where and kind in ("float_parsing", "float_type"):
        return f"“{where}” must be a number."
    if where and kind in ("bool_parsing", "bool_type"):
        return f"“{where}” must be true or false."
    if where and kind in ("string_type", "string_too_long"):
        return f"“{where}” must be text" + (" (shorter)." if kind == "string_too_long" else ".")
    return f"Invalid {where or 'request'}: {first.get('msg', 'check the values you sent')}."


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse({"detail": validation_text(exc.errors())}, status_code=422)


@app.exception_handler(Exception)
@app.exception_handler(asyncpg.PostgresError)     # these three are also registered by class, so a route's database or
@app.exception_handler(asyncpg.InterfaceError)    # file error is answered here without uvicorn logging a second traceback
@app.exception_handler(OSError)
async def server_error(request: Request, exc: Exception):
    ref = secrets.token_hex(3)
    status, detail, headers = explain_exception(exc, ref)
    if status == 503:
        log.error("Database unavailable [ref %s] on %s %s: %s: %s", ref, request.method, request.url.path,
                  exc.__class__.__name__, keys.scrub(str(exc), "")[:200])
    elif status == 400:
        log.warning("Value refused by the database [ref %s] on %s %s: %s", ref, request.method, request.url.path,
                    keys.scrub(str(exc), "")[:200])
    else:
        log.exception("Unhandled error [ref %s] on %s %s", ref, request.method, request.url.path)
    if _wants_json(request):
        return JSONResponse({"detail": detail, "ref": ref}, status_code=status, headers=headers)
    return _error_page(status, "Not available right now" if status == 503 else "Something went wrong", detail)


# ---------------------------------------------------------------- middleware

@app.middleware("http")
async def no_cache(request, call_next):
    # DNS-rebinding guard: only answer requests addressed to this machine.
    if not _host_ok(request.headers.get("host", "")):
        return JSONResponse({"detail": "Unknown host. Agents Atelier only answers on localhost."}, status_code=421)
    # Another website can't change anything here: state-changing requests must come from our own pages.
    if request.method not in ("GET", "HEAD", "OPTIONS") and not _origin_ok(request.headers.get("host", ""), request.headers.get("origin")):
        return JSONResponse({"detail": "Requests from other websites aren't allowed."}, status_code=403)
    size = request.headers.get("content-length")
    limit = MAX_SITE_IMPORT * 4 // 3 + 65536 if request.url.path == "/api/projects/import" else MAX_BODY   # base64 adds a third
    if size and size.isdigit() and int(size) > limit:
        return JSONResponse({"detail": f"That request is {human_size(int(size))}; the limit is {human_size(limit)}."}, status_code=413)
    denied = await _check_access(request)
    if denied:
        return denied
    if request.url.path.startswith("/api/") and request.url.path not in RATE_FREE:
        wait = limits.API.wait(limits.key_for(request))
        if wait:
            return JSONResponse({"detail": f"Too many requests. Slow down and try again in {wait} seconds."}, status_code=429,
                                headers={"Retry-After": str(wait)})
    response = await call_next(request)
    if request.url.path.startswith("/work") or re.match(r"^/s/[^/]+/site(?:/|$)", request.url.path):
        response.headers["Cache-Control"] = "no-store"
        # Generated pages always run in an isolated origin: their JavaScript can't call this app's API,
        # whether they're shown in the preview, a thumbnail or their own tab.
        # tel:, mailto: and whatsapp: links may open their app, and wa.me / t.me links open in a normal new tab.
        response.headers["Content-Security-Policy"] = ("sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads "
                                                       "allow-top-navigation-to-custom-protocols allow-popups-to-escape-sandbox")
        response.headers["X-Content-Type-Options"] = "nosniff"
    elif request.url.path.startswith("/api/"):
        response.headers["X-Content-Type-Options"] = "nosniff"
    elif request.url.path.startswith("/static/"):
        # Revalidate every time (cheap: ETag → 304) so a refresh always picks up fixed scripts and styles.
        response.headers["Cache-Control"] = "no-cache"
    return response


FRAMEABLE = re.compile(r"^/(?:work(?:/|$)|s/[^/]+/site(?:/|$))")


@app.middleware("http")
async def no_framing(request: Request, call_next):
    """No other website may show the app in a frame (clickjacking), whatever answered: a page, an error, a redirect.
    Only the generated sites are framed, by the app's own pages (the studio preview, thumbnails, the share page),
    and they keep their own sandbox. Added last, so it wraps every other middleware and sees every response."""
    response = await call_next(request)
    if not FRAMEABLE.match(request.url.path):
        response.headers["X-Frame-Options"] = "DENY"
        csp = response.headers.get("Content-Security-Policy")
        response.headers["Content-Security-Policy"] = f"{csp}; frame-ancestors 'none'" if csp else "frame-ancestors 'none'"
    return response


# ---------------------------------------------------------------- routes (one router per area, see routes/)

for area in (routes.pages, routes.account, routes.admin, routes.agents, routes.projects, routes.market, routes.share,
             routes.files, routes.ws):
    app.include_router(area.router)

app.mount("/static", StaticFiles(directory=STATIC), name="static")
