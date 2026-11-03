"""Agents Atelier server.

Each project lives in work/<uuid>/ (the site the agents build).
Its metadata and chat log live in work/.projects/<uuid>.json and <uuid>.events.jsonl.

- GET    /                           landing page
- GET    /studio                     redirects to /projects (the studio opens only through a project)
- GET    /p/<uuid>                   the studio (chat + live preview) for one project
- GET    /projects                   all projects
- GET    /agents                     view and edit the agent team
- GET    /guide                      how to use Agents Atelier
- GET    /work/<uuid>/...            the project's site, served live
- GET    /api/projects               list projects (newest activity first)
- POST   /api/projects               create a project -> {id}
- GET    /api/projects/<id>          project info + saved chat events
- DELETE /api/projects/<id>          delete a project
- GET    /api/projects/<id>/files    files in the project, with content (Code tab)
- GET    /api/projects/<id>/download the project's site as a .zip (409 while agents are working)
- PUT    /api/projects/<id>/files    save one edited file {path, content}
- POST   /api/projects/<id>/uploads  upload a reference image {name, data: data-URL} -> {path, url}
- GET    /api/projects/<id>/versions               list code versions (newest first)
- POST   /api/projects/<id>/versions               save a version now {label}
- GET    /api/projects/<id>/versions/<n>/diff      changes from version n to the current code (or ?against=m)
- POST   /api/projects/<id>/versions/<n>/restore   make version n the current code (current is saved first)
- GET    /api/projects/<id>/versions/<n>/download  version n as a .zip
- GET    /api/stats?project=<id>     cost, time and tokens per agent (one project, or all)
- GET/POST/DELETE /api/presets       team presets (built-in + saved)
- WS     /ws/<id>                    send {"type":"prompt","text":...,"agents":[...],"images":[...]}, {"type":"stop"},
                                     {"type":"continue"}, {"type":"restart"} or {"type":"retry","agent":id,"note":...};
                                     receive live events

Run state is saved in the project's meta as "run": request, agents, done, notes, current agent and status
(running · stopped · error · interrupted · done), so a stopped run can be continued or started again.
"""

import asyncio
import base64
import errno
import io
import json
import logging
import secrets
import re
import zipfile
import os
import shutil
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles

import agents as team_files
from agents import default_ids, load_agents, load_common
from versions import Versions

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
META = WORK / ".projects"
STATIC = Path(__file__).resolve().parent / "static"
META.mkdir(parents=True, exist_ok=True)
VERS = Versions(WORK)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agents-atelier")

MAX_BODY = 8 * 1024 * 1024          # largest request body we accept (an image upload is ~6.7 MB as base64)
MAX_IMAGE = 5 * 1024 * 1024         # largest reference image
MAX_EDIT = 500 * 1024               # largest file the Code tab will load or save
AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "900"))   # seconds before a stuck agent is stopped
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
TEXT_EXT = {".html", ".css", ".js", ".md", ".svg", ".json", ".txt", ".xml", ".webmanifest"}

CLAUDE = os.environ.get("CLAUDE_BIN", "claude")
MODEL = os.environ.get("AGENT_MODEL")  # e.g. "sonnet" for faster runs; default = your Claude Code default

app = FastAPI()


def human_size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


def write_json(path: Path, data):
    """Write JSON atomically, so a crash mid-write never leaves a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def safely(what: str, fn, *args, **kwargs):
    """Run a side task (versioning, stats) without letting its failure break a run."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        log.exception("%s failed", what)
        return None


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


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
    if exc.status_code == 404 and detail == "Not Found":
        detail = "This page or file doesn't exist."
    if _wants_json(request):
        return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))
    return _error_page(exc.status_code, "Not found" if exc.status_code == 404 else "Something's not right", detail)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    first = errors[0] if errors else {}
    if first.get("type") == "json_invalid":
        msg = "The request body isn't valid JSON."
    else:
        where = " → ".join(str(x) for x in first.get("loc", []) if x not in ("body", "query", "path"))
        msg = f"Invalid {where or 'request'}: {first.get('msg', 'check the values you sent')}."
    return JSONResponse({"detail": msg}, status_code=422)


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    ref = secrets.token_hex(3)
    log.exception("Unhandled error [ref %s] on %s %s", ref, request.method, request.url.path)
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        detail = "The disk is full, so the server couldn't save. Free some space and try again."
    elif isinstance(exc, OSError):
        detail = f"The server couldn't read or write a file ({exc.strerror or exc}). [ref {ref}]"
    else:
        detail = f"Something went wrong on the server. Details are in the server log under ref {ref}."
    if _wants_json(request):
        return JSONResponse({"detail": detail, "ref": ref}, status_code=500)
    return _error_page(500, "Something went wrong", detail)

# Live state per project: running task, claude process, connected browser tabs.
runs: dict[str, dict] = {}


def live(pid: str) -> dict:
    return runs.setdefault(pid, {"task": None, "proc": None, "clients": set()})


def is_busy(pid: str) -> bool:
    t = runs.get(pid, {}).get("task")
    return bool(t and not t.done())


# ---------------------------------------------------------------- storage

def check_id(pid: str) -> str:
    try:
        return str(uuid.UUID(pid))
    except ValueError:
        raise HTTPException(404, "Unknown project")


def meta_path(pid: str) -> Path:
    return META / f"{pid}.json"


def load_meta(pid: str) -> dict:
    p = meta_path(check_id(pid))
    if not p.exists():
        raise HTTPException(404, "This project doesn't exist (it may have been deleted).")
    try:
        return json.loads(p.read_text())
    except ValueError:
        log.exception("Damaged project file %s", p)
        raise HTTPException(500, f"This project's data file is damaged ({p.relative_to(ROOT)}). Restore it or delete the project.")


def save_meta(meta: dict):
    meta["updated"] = time.time()
    write_json(meta_path(meta["id"]), meta)


def clean_details(body: dict, *, partial: bool = False) -> dict:
    """Validate a project's name and description. Both are required for every project."""
    out = {}
    for key, limit in (("name", 80), ("description", 300)):
        if partial and key not in body:
            continue
        value = " ".join(str(body.get(key) or "").split())
        if not value:
            raise HTTPException(400, f"Every project needs a {key}.")
        if len(value) > limit:
            raise HTTPException(400, f"Keep the {key} under {limit} characters.")
        out[key] = value
    return out


def create_project(name: str, description: str) -> dict:
    pid = str(uuid.uuid4())
    (WORK / pid).mkdir(parents=True)
    now = time.time()
    meta = {"id": pid, "name": name, "description": description, "created": now, "updated": now,
            "history": [], "agents": default_ids()}
    save_meta(meta)
    return meta


def events_file(pid: str) -> Path:
    return META / f"{pid}.events.jsonl"


def log_event(pid: str, event: dict):
    """Number every event and save chat-relevant ones, so the chat can be restored later."""
    st = live(pid)
    if "seq" not in st:
        f = events_file(pid)
        st["seq"] = len(f.read_text().splitlines()) if f.exists() else 0
    if event["type"] == "file_changed":
        event["seq"] = st["seq"]  # not saved; shares the last number
        return
    st["seq"] += 1
    event["seq"] = st["seq"]
    event["ts"] = time.time()
    with open(events_file(pid), "a") as f:
        f.write(json.dumps(event) + "\n")


def migrate_old_layout():
    """Move files from the old single-project layout (work/index.html...) into a project."""
    old = [p for p in WORK.iterdir() if p.is_file()]
    if not old:
        return
    meta = create_project("My first project", "Moved here from the old single-project layout.")
    for p in old:
        shutil.move(str(p), WORK / meta["id"] / p.name)


migrate_old_layout()

RESUMABLE = ("stopped", "error", "interrupted")


def mark_interrupted_runs():
    """A run that was 'running' when the server stopped can't still be running: mark it interrupted."""
    for p in META.glob("*.json"):
        try:
            m = json.loads(p.read_text())
        except ValueError:
            log.warning("Skipping damaged project file %s", p)
            continue
        if (m.get("run") or {}).get("status") == "running":
            m["run"]["status"] = "interrupted"
            write_json(p, m)


mark_interrupted_runs()


def update_run(pid: str, **changes) -> dict:
    meta = load_meta(pid)
    run = meta.setdefault("run", {})
    run.update(changes, updated=time.time())
    save_meta(meta)
    return run


def run_summary(run: dict) -> dict:
    """What the browser needs to show a paused run and offer Continue / Start again."""
    if not run:
        return {}
    agents, done = run.get("agents", []), run.get("done", [])
    nxt = next((a for a in agents if a not in done), None)
    return {"status": run.get("status"), "request": run.get("request", ""), "agents": agents, "done": done,
            "current": run.get("current") or nxt, "next": nxt, "error": run.get("error", ""),
            "resumable": run.get("status") in RESUMABLE and nxt is not None}


# ---------------------------------------------------------------- http

@app.middleware("http")
async def no_cache(request, call_next):
    size = request.headers.get("content-length")
    if size and size.isdigit() and int(size) > MAX_BODY:
        return JSONResponse({"detail": f"That request is {human_size(int(size))}; the limit is {human_size(MAX_BODY)}."}, status_code=413)
    response = await call_next(request)
    if request.url.path.startswith("/work"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
async def landing():
    return FileResponse(STATIC / "landing.html")


@app.get("/studio")
async def studio():
    # The studio only opens through a project (/p/<uuid>); old links land on the projects list.
    return RedirectResponse("/projects")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(STATIC / "logo.svg", media_type="image/svg+xml")


@app.get("/projects")
async def projects_page():
    return FileResponse(STATIC / "projects.html")


@app.get("/agents")
async def agents_page():
    return FileResponse(STATIC / "agents.html")


@app.get("/guide")
async def guide_page():
    return FileResponse(STATIC / "guide.html")


@app.get("/p/{pid}")
async def project_page(pid: str):
    load_meta(pid)
    return FileResponse(STATIC / "studio.html")


@app.get("/api/agents")
async def list_agents(full: bool = False):
    keys = ("id", "name", "icon", "color", "default", "description", "model") + (("role", "file") if full else ())
    return [{k: a[k] for k in keys} for a in load_agents()]


def _agent_call(fn, *args):
    try:
        return fn(*args)
    except KeyError:
        raise HTTPException(404, "Unknown agent")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/agents/common")
async def get_common():
    return {"text": load_common()}


@app.put("/api/agents/common")
async def put_common(body: dict):
    text = str(body.get("text", "")).strip()
    if len(text) < 10:
        raise HTTPException(400, "The shared rules are too short.")
    team_files.save_common(text)
    return {"text": load_common()}


@app.post("/api/agents/order")
async def order_agents(body: dict):
    _agent_call(team_files.reorder_agents, list(body.get("ids") or []))
    return await list_agents(full=True)


@app.post("/api/agents")
async def add_agent(body: dict):
    return _agent_call(team_files.create_agent, body, body.get("position"))


@app.put("/api/agents/{agent_id}")
async def edit_agent(agent_id: str, body: dict):
    return _agent_call(team_files.update_agent, agent_id, body)


@app.delete("/api/agents/{agent_id}")
async def remove_agent(agent_id: str):
    _agent_call(team_files.delete_agent, agent_id)
    return {"ok": True}


@app.get("/api/projects")
async def list_projects(page: int | None = None, per_page: int = 12, q: str = "", sort: str = "updated", status: str = "all"):
    """Without `page`: every project (newest activity first), as a list.
    With `page`: one page of results plus totals, after search (`q`), `status` filter and `sort`."""
    projects = all_projects()
    if page is None:
        return projects

    counts = {"all": len(projects), "working": sum(p["busy"] for p in projects),
              "built": sum(p["has_preview"] for p in projects), "empty": sum(not p["has_preview"] for p in projects)}
    needle = q.strip().lower()
    if needle:
        projects = [p for p in projects if needle in p["name"].lower() or needle in p["description"].lower() or p["id"].startswith(needle)]
    if status == "working":
        projects = [p for p in projects if p["busy"]]
    elif status == "built":
        projects = [p for p in projects if p["has_preview"]]
    elif status == "empty":
        projects = [p for p in projects if not p["has_preview"]]
    if sort == "name":
        projects.sort(key=lambda p: p["name"].lower())
    elif sort == "created":
        projects.sort(key=lambda p: p["created"], reverse=True)

    per_page = max(1, min(per_page, 96))
    total = len(projects)
    pages = max(1, -(-total // per_page))
    page = max(1, min(page, pages))
    start = (page - 1) * per_page
    return {"items": projects[start:start + per_page], "page": page, "per_page": per_page,
            "pages": pages, "total": total, "counts": counts,
            "prompts": sum(p["prompts"] for p in all_projects())}


def all_projects() -> list[dict]:
    projects = []
    for p in META.glob("*.json"):
        try:
            m = json.loads(p.read_text())
            m["id"], m["name"]
        except (ValueError, KeyError, TypeError):
            log.warning("Skipping damaged project file %s", p)
            continue
        folder = WORK / m["id"]
        files = [f for f in folder.rglob("*") if f.is_file()] if folder.exists() else []
        projects.append({"id": m["id"], "name": m["name"], "description": m.get("description", ""), "created": m["created"],
                         "updated": m["updated"], "busy": is_busy(m["id"]),
                         "prompts": len(m.get("history", [])), "agents": len(m.get("agents") or default_ids()),
                         "files": len(files), "size": sum(f.stat().st_size for f in files),
                         "has_preview": (folder / "index.html").exists(),
                         "paused": (m.get("run") or {}).get("status") in RESUMABLE and not is_busy(m["id"])})
    return sorted(projects, key=lambda m: m["updated"], reverse=True)


@app.post("/api/projects")
async def new_project(body: dict):
    return create_project(**clean_details(body))


@app.get("/api/projects/{pid}")
async def get_project(pid: str):
    meta = load_meta(pid)
    f = events_file(meta["id"])
    events = []
    for line in (f.read_text().splitlines() if f.exists() else []):
        try:
            events.append(json.loads(line))
        except ValueError:
            log.warning("Skipping a damaged chat line in %s", f)
    meta.setdefault("agents", default_ids())
    meta.setdefault("description", "")
    return {**meta, "busy": is_busy(meta["id"]), "events": events, "run": run_summary(meta.get("run"))}


@app.patch("/api/projects/{pid}")
async def update_project(pid: str, body: dict):
    meta = load_meta(pid)
    meta.update(clean_details(body, partial=True))
    if isinstance(body.get("agents"), list):
        meta["agents"] = [a["id"] for a in load_agents() if a["id"] in body["agents"]]
    save_meta(meta)
    return meta


@app.delete("/api/projects/{pid}")
async def delete_project(pid: str):
    meta = load_meta(pid)
    if is_busy(meta["id"]):
        raise HTTPException(409, "Agents are working on this project. Stop them first.")
    shutil.rmtree(WORK / meta["id"], ignore_errors=True)
    meta_path(meta["id"]).unlink(missing_ok=True)
    events_file(meta["id"]).unlink(missing_ok=True)
    VERS.delete_all(meta["id"])
    runs.pop(meta["id"], None)
    return {"ok": True}


@app.get("/api/projects/{pid}/files")
async def list_files(pid: str):
    """Every file with its size. Text content is included only for files small enough to edit."""
    folder = WORK / load_meta(pid)["id"]
    files = []
    for p in sorted(folder.rglob("*")) if folder.exists() else []:
        if not p.is_file():
            continue
        size = p.stat().st_size
        item = {"path": str(p.relative_to(folder)), "size": size, "content": None,
                "kind": "image" if p.suffix.lower() in IMAGE_EXT else "text" if p.suffix.lower() in TEXT_EXT else "other"}
        if item["kind"] == "image":
            item["url"] = f"/work/{folder.name}/{item['path']}"
        elif size > MAX_EDIT:
            item["too_large"] = True
        else:
            try:
                item["content"] = p.read_text()
            except UnicodeDecodeError:
                item["kind"] = "binary"
        files.append(item)
    return files


@app.get("/api/projects/{pid}/download")
async def download(pid: str):
    meta = load_meta(pid)
    if is_busy(meta["id"]):
        raise HTTPException(409, "Agents are still working on this project. Download it when they finish.")
    folder = WORK / meta["id"]
    buf = io.BytesIO()
    slug = re.sub(r"[^a-z0-9]+", "-", meta["name"].lower()).strip("-")[:40] or "project"
    root = f"{slug}-{meta['id'][:8]}"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                z.write(p, f"{root}/{p.relative_to(folder)}")
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{root}.zip"'})


# ---------------------------------------------------------------- presets

@app.get("/api/presets")
async def get_presets():
    return team_files.load_presets()


@app.post("/api/presets")
async def add_preset(body: dict):
    return _agent_call(team_files.save_preset, body.get("name", ""), list(body.get("agents") or []))


@app.delete("/api/presets/{preset_id}")
async def remove_preset(preset_id: str):
    _agent_call(team_files.delete_preset, preset_id)
    return {"ok": True}


# ---------------------------------------------------------------- editing & uploads

def safe_path(pid: str, rel: str) -> Path:
    folder = (WORK / pid).resolve()
    target = (folder / rel).resolve()
    if folder not in target.parents:
        raise HTTPException(400, "That path is outside the project.")
    return target


@app.put("/api/projects/{pid}/files")
async def save_file(pid: str, body: dict):
    pid = load_meta(pid)["id"]
    if is_busy(pid):
        raise HTTPException(409, "Agents are working on this project. Edit when they finish.")
    target = safe_path(pid, str(body.get("path", "")))
    if target.suffix.lower() not in TEXT_EXT:
        raise HTTPException(400, "Only text files (html, css, js, md, svg, json…) can be edited here.")
    content = body.get("content")
    if not isinstance(content, str):
        raise HTTPException(400, "Send the file's content as text.")
    size = len(content.encode())
    if size > MAX_EDIT:
        raise HTTPException(413, f"This file would be {human_size(size)}; files edited here are limited to {human_size(MAX_EDIT)}.")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(content)
        os.replace(tmp, target)
    except OSError as e:
        raise HTTPException(507 if e.errno == errno.ENOSPC else 500, f"Couldn't save {target.name}: {e.strerror or e}.")
    meta = load_meta(pid); save_meta(meta)  # bump "updated"
    return {"ok": True, "path": str(target.relative_to((WORK / pid).resolve())), "saved": time.time()}


@app.post("/api/projects/{pid}/uploads")
async def upload_image(pid: str, body: dict):
    pid = load_meta(pid)["id"]
    data = str(body.get("data", ""))
    m = re.match(r"^data:(image/[a-z+.-]+);base64,(.+)$", data, re.S)
    if not m or m.group(1) not in IMAGE_TYPES:
        raise HTTPException(400, "Upload a PNG, JPG, WebP or GIF image.")
    if len(m.group(2)) * 3 // 4 > MAX_IMAGE + 4:
        raise HTTPException(413, f"This image is about {human_size(len(m.group(2)) * 3 // 4)}; the limit is {human_size(MAX_IMAGE)}.")
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except ValueError:
        raise HTTPException(400, "The image data is damaged. Try attaching it again.")
    if not raw:
        raise HTTPException(400, "That image is empty.")
    stem = re.sub(r"[^a-z0-9]+", "-", Path(str(body.get("name") or "image")).stem.lower()).strip("-")[:40] or "image"
    name = f"{stem}-{secrets.token_hex(3)}.{IMAGE_TYPES[m.group(1)]}"
    folder = WORK / pid / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        (folder / name).write_bytes(raw)
    except OSError as e:
        raise HTTPException(507 if e.errno == errno.ENOSPC else 500, f"Couldn't save the image: {e.strerror or e}.")
    return {"path": f"uploads/{name}", "url": f"/work/{pid}/uploads/{name}", "size": len(raw)}


# ---------------------------------------------------------------- versions

def _version_call(fn, *args):
    try:
        return fn(*args)
    except KeyError:
        raise HTTPException(404, "Unknown version")


@app.get("/api/projects/{pid}/versions")
async def list_versions(pid: str):
    return VERS.list(load_meta(pid)["id"])


@app.post("/api/projects/{pid}/versions")
async def save_version(pid: str, body: dict | None = None):
    pid = load_meta(pid)["id"]
    if is_busy(pid):
        raise HTTPException(409, "Agents are working. Versions are saved automatically after each agent.")
    label = " ".join(str((body or {}).get("label") or "Saved by you").split())[:80]
    v = VERS.snapshot(pid, label, kind="manual")
    if not v:
        raise HTTPException(400, "Nothing changed since the last version.")
    return v


@app.get("/api/projects/{pid}/versions/{n}/diff")
async def version_diff(pid: str, n: int, against: int | None = None):
    pid = load_meta(pid)["id"]
    _version_call(VERS.get, pid, n)
    if against is not None:
        _version_call(VERS.get, pid, against)
    return _version_call(VERS.diff, pid, n, against)


@app.post("/api/projects/{pid}/versions/{n}/restore")
async def restore_version(pid: str, n: int):
    pid = load_meta(pid)["id"]
    if is_busy(pid):
        raise HTTPException(409, "Agents are working on this project. Restore when they finish.")
    backup = _version_call(VERS.restore, pid, n)
    await broadcast(pid, {"type": "restored", "version": n, "backup": backup["n"] if backup else None})
    return {"ok": True, "backup": backup}


@app.get("/api/projects/{pid}/versions/{n}/download")
async def download_version(pid: str, n: int):
    meta = load_meta(pid)
    _version_call(VERS.get, meta["id"], n)
    folder = VERS.folder(meta["id"], n)
    buf = io.BytesIO()
    slug = re.sub(r"[^a-z0-9]+", "-", meta["name"].lower()).strip("-")[:40] or "project"
    root = f"{slug}-v{n}"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                z.write(p, f"{root}/{p.relative_to(folder)}")
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{root}.zip"'})


# ---------------------------------------------------------------- stats

def record_stat(pid: str, entry: dict):
    meta = load_meta(pid)
    stats = meta.setdefault("stats", [])
    stats.append(entry)
    del stats[:-500]
    write_json(meta_path(pid), meta)   # keep "updated" as is


@app.get("/api/stats")
async def get_stats(project: str | None = None):
    if project:
        metas = [load_meta(project)]
    else:
        metas = []
        for p in META.glob("*.json"):
            try:
                metas.append(json.loads(p.read_text()))
            except ValueError:
                log.warning("Skipping damaged project file %s", p)
    entries = [dict(e, project=m["id"], project_name=m["name"]) for m in metas for e in m.get("stats", [])]
    entries.sort(key=lambda e: e["ts"])
    names = {a["id"]: a for a in load_agents()}
    by_agent = {}
    for e in entries:
        b = by_agent.setdefault(e["agent"], {"agent": e["agent"], "name": names.get(e["agent"], {}).get("name", e["agent"]),
                                              "icon": names.get(e["agent"], {}).get("icon", "bot"),
                                              "color": names.get(e["agent"], {}).get("color", "#766a7c"),
                                              "runs": 0, "done": 0, "cost": 0.0, "seconds": 0.0, "tokens_in": 0, "tokens_out": 0, "models": set()})
        b["runs"] += 1
        b["done"] += e["status"] == "done"
        b["cost"] += e.get("cost") or 0
        b["seconds"] += e.get("seconds") or 0
        b["tokens_in"] += e.get("tokens_in") or 0
        b["tokens_out"] += e.get("tokens_out") or 0
        if e.get("model"):
            b["models"].add(e["model"])
    order = [a["id"] for a in load_agents()]
    agents = sorted(by_agent.values(), key=lambda b: order.index(b["agent"]) if b["agent"] in order else 99)
    for b in agents:
        b["models"] = sorted(b["models"])
        b["avg_seconds"] = b["seconds"] / b["runs"] if b["runs"] else 0
        b["avg_cost"] = b["cost"] / b["runs"] if b["runs"] else 0
    totals = {"runs": len(entries), "cost": sum(e.get("cost") or 0 for e in entries),
              "seconds": sum(e.get("seconds") or 0 for e in entries),
              "tokens_in": sum(e.get("tokens_in") or 0 for e in entries),
              "tokens_out": sum(e.get("tokens_out") or 0 for e in entries),
              "failed": sum(e["status"] != "done" for e in entries)}
    return {"totals": totals, "agents": agents, "recent": entries[-30:][::-1]}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/work", StaticFiles(directory=WORK, html=True), name="work")


# ---------------------------------------------------------------- agents

async def broadcast(pid: str, event: dict):
    event["project"] = pid
    log_event(pid, event)
    st = live(pid)
    for ws in list(st["clients"]):
        try:
            await ws.send_json(event)
        except Exception:
            st["clients"].discard(ws)


def short_path(path: str, folder: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(folder))
    except ValueError:
        return Path(path).name


async def run_agent(pid: str, agent: dict, common: str, request: str, notes: list[str], meta: dict,
                    images: list[str] = ()) -> str:
    """Run one agent as a `claude -p` process inside the project folder and stream what it does."""
    folder = WORK / pid
    past = "\n".join(f"- {h}" for h in meta["history"][-6:]) or "(none)"
    team_notes = "\n".join(notes) or "(you are the first agent)"
    images_note = ("\nReference images from the user (open each one with the Read tool before you start; "
                   "they are in the project folder and may be used on the page):\n"
                   + "\n".join(f"- {i}" for i in images) + "\n") if images else ""
    prompt = f"""Project: {meta["name"]}
About this project: {meta.get("description") or "(no description)"}

Earlier requests in this project:
{past}

Current user request:
{request}

Notes from agents who already worked on this request:
{team_notes}
{images_note}
Do your job now."""

    cmd = [
        CLAUDE, "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--append-system-prompt", common + "\n\n" + agent["role"],
        "--allowedTools", "Read", "Write", "Edit", "Glob", "Grep",
        "--disallowedTools", "Bash", "WebFetch", "WebSearch", "Task",
        "--permission-mode", "acceptEdits",
    ]
    model = agent.get("model") or MODEL
    if model:
        cmd += ["--model", model]

    st = live(pid)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=folder,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )
    except FileNotFoundError:
        raise RuntimeError(f"Claude Code wasn't found (tried “{CLAUDE}”). Install it, or set CLAUDE_BIN to its path, then restart the server.")
    except PermissionError:
        raise RuntimeError(f"Claude Code at “{CLAUDE}” isn't executable. Check its permissions or set CLAUDE_BIN.")
    st["proc"] = proc
    final_text = ""
    pending_edits = {}  # tool_use_id -> file
    started = time.time()
    info = {"model": model or ""}
    outcome = "error"
    stderr_task = asyncio.create_task(proc.stderr.read())   # drain stderr so a chatty process can't stall
    try:
        final_text = await asyncio.wait_for(_stream_agent(pid, agent, proc, folder, pending_edits, info, stderr_task),
                                            timeout=AGENT_TIMEOUT)
        outcome = "done"
        return final_text
    except asyncio.TimeoutError:
        raise RuntimeError(f"The {agent['name']} agent ran for more than {AGENT_TIMEOUT // 60} minutes and was stopped. "
                           "Try again, or raise AGENT_TIMEOUT.")
    except asyncio.CancelledError:
        outcome = "stopped"
        raise
    finally:
        if proc.returncode is None:
            proc.kill()
        stderr_task.cancel()
        safely("Recording stats", record_stat, pid, {"agent": agent["id"], "status": outcome, "ts": started,
                                                     "request": request[:100], "seconds": round(time.time() - started, 1), **info})


def explain_failure(text: str) -> str:
    """Turn Claude Code's raw error output into something actionable."""
    low = text.lower()
    if "not logged in" in low or "/login" in low or "invalid api key" in low or "authentication" in low:
        return "Claude Code isn't logged in. Run `claude` in a terminal, log in, then try again."
    if "usage limit" in low or "rate limit" in low or "429" in low or "overloaded" in low:
        return "Claude is rate-limited or overloaded right now. Wait a little, then press Continue."
    if "model" in low and ("not found" in low or "invalid" in low or "not available" in low):
        return f"The model for this agent isn't available: {text.strip()[-200:]} — change it on the Agents page."
    return text.strip()[-500:] or "The agent stopped unexpectedly."


async def _stream_agent(pid: str, agent: dict, proc, folder: Path, pending_edits: dict, info: dict, stderr_task) -> str:
    st = live(pid)
    final_text = ""
    async for raw in proc.stdout:
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        kind = ev.get("type")

        if kind == "system" and ev.get("model"):
            info["model"] = ev["model"]
        elif kind == "assistant":
            for block in ev["message"].get("content", []):
                if block.get("type") == "text" and block["text"].strip():
                    final_text = block["text"].strip()
                    await broadcast(pid, {"type": "agent_text", "agent": agent["id"], "text": final_text})
                elif block.get("type") == "tool_use":
                    inp = block.get("input", {})
                    if inp.get("file_path"):
                        target = short_path(inp["file_path"], folder)
                    else:
                        target = inp.get("pattern") or inp.get("path") or ""
                    if block["name"] in ("Write", "Edit"):
                        pending_edits[block["id"]] = target
                    await broadcast(pid, {"type": "tool", "agent": agent["id"],
                                          "tool": block["name"], "target": target})

        elif kind == "user":
            for block in ev["message"].get("content", []):
                if block.get("type") == "tool_result" and block.get("tool_use_id") in pending_edits:
                    await broadcast(pid, {"type": "file_changed", "agent": agent["id"],
                                          "file": pending_edits.pop(block["tool_use_id"])})

        elif kind == "result":
            usage = ev.get("usage") or {}
            info.update(cost=round(ev.get("total_cost_usd") or 0, 5), turns=ev.get("num_turns"),
                        api_seconds=round((ev.get("duration_api_ms") or 0) / 1000, 1),
                        tokens_in=(usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
                                  + (usage.get("cache_read_input_tokens") or 0),
                        tokens_out=usage.get("output_tokens") or 0)
            if ev.get("result"):
                final_text = ev["result"].strip()
            if ev.get("is_error"):
                raise RuntimeError(explain_failure(final_text or "The agent reported an error."))

    await proc.wait()
    st["proc"] = None
    if proc.returncode != 0:
        err = (await stderr_task).decode(errors="replace")
        raise RuntimeError(explain_failure(err or f"Claude Code exited with code {proc.returncode}."))
    return final_text


async def run_chain(pid: str, request: str, agent_ids: list[str], resume: bool = False, images: list[str] = ()):
    """Run the team in order. With resume=True, skip the agents that already finished in the saved run
    and give the rest the notes those agents left."""
    st = live(pid)
    meta = load_meta(pid)
    team = [a for a in load_agents() if a["id"] in agent_ids]  # snapshot of agents/ at run start
    common = load_common()
    prev = meta.get("run") or {}
    done = [i for i in prev.get("done", []) if i in agent_ids] if resume else []
    notes = list(prev.get("notes", [])) if resume else []
    meta["agents"] = agent_ids
    images = list(images) or (list(prev.get("images", [])) if resume else [])
    meta["run"] = {"request": request, "images": images, "agents": [a["id"] for a in team], "done": done, "notes": notes,
                   "current": None, "status": "running", "error": "",
                   "started": prev.get("started", time.time()) if resume else time.time(), "updated": time.time()}
    save_meta(meta)

    await broadcast(pid, {"type": "run_start", "request": request, "name": meta["name"],
                          "agents": meta["run"]["agents"], "done": done, "resumed": resume})
    if safely("Snapshot", VERS.snapshot, pid, f"Before: {request[:60]}", kind="before", request=request):
        await broadcast(pid, {"type": "versions"})
    try:
        for agent in [a for a in team if a["id"] not in done]:
            update_run(pid, current=agent["id"])
            await broadcast(pid, {"type": "agent_start", "agent": agent["id"]})
            summary = await run_agent(pid, agent, common, request, notes, load_meta(pid), images)
            notes.append(f"- {agent['name']} agent: {summary}")
            done.append(agent["id"])
            update_run(pid, done=done, notes=notes, current=None)
            v = safely("Snapshot", VERS.snapshot, pid, f"After {agent['name']}", kind="agent", agent=agent["id"], request=request)
            await broadcast(pid, {"type": "agent_done", "agent": agent["id"], "text": summary,
                                  "stat": last_stat(pid), "version": v["n"] if v else None})
        meta = load_meta(pid)
        meta["history"].append(request)
        meta["run"].update(status="done", current=None, updated=time.time())
        save_meta(meta)
        await broadcast(pid, {"type": "run_done"})
    except asyncio.CancelledError:
        if st["proc"] and st["proc"].returncode is None:
            st["proc"].kill()
        run = update_run(pid, status="stopped")
        await broadcast(pid, {"type": "run_stopped", "run": run_summary(run)})
    except Exception as e:
        run = update_run(pid, status="error", error=str(e)[-500:])
        await broadcast(pid, {"type": "error", "text": str(e), "run": run_summary(run)})
    finally:
        st["proc"] = None


def last_stat(pid: str) -> dict:
    try:
        stats = load_meta(pid).get("stats") or []
    except HTTPException:
        return {}
    return stats[-1] if stats else {}


async def run_retry(pid: str, agent: dict, request: str, note: str):
    """Run a single agent again on the last request, with an optional extra instruction.
    The saved run state (for Continue / Start again) is left untouched."""
    st = live(pid)
    meta = load_meta(pid)
    run = meta.get("run") or {}
    notes = [n for n in run.get("notes", []) if not n.startswith(f"- {agent['name']} agent:")]
    full = request + (f"\n\nRetry — the user asked the {agent['name']} agent to redo its step with this instruction: {note}"
                      if note else f"\n\nRetry — the user asked the {agent['name']} agent to redo its step and do it better.")
    await broadcast(pid, {"type": "run_start", "request": request, "name": meta["name"], "agents": [agent["id"]],
                          "retry": True, "note": note})
    safely("Snapshot", VERS.snapshot, pid, f"Before retrying {agent['name']}", kind="before", request=request)
    try:
        await broadcast(pid, {"type": "agent_start", "agent": agent["id"], "retry": True})
        summary = await run_agent(pid, agent, load_common(), full, notes, meta, run.get("images", []))
        v = safely("Snapshot", VERS.snapshot, pid, f"After {agent['name']} (retry)", kind="agent", agent=agent["id"], request=request)
        await broadcast(pid, {"type": "agent_done", "agent": agent["id"], "text": summary, "stat": last_stat(pid),
                              "version": v["n"] if v else None})
        await broadcast(pid, {"type": "run_done", "retry": True})
    except asyncio.CancelledError:
        if st["proc"] and st["proc"].returncode is None:
            st["proc"].kill()
        await broadcast(pid, {"type": "run_stopped", "retry": True, "run": run_summary(load_meta(pid).get("run"))})
    except Exception as e:
        await broadcast(pid, {"type": "error", "text": str(e), "retry": True})
    finally:
        st["proc"] = None


@app.websocket("/ws/{pid}")
async def ws_endpoint(ws: WebSocket, pid: str):
    await ws.accept()
    try:
        pid = load_meta(pid)["id"]
    except HTTPException:
        await ws.close(code=4404)
        return
    st = live(pid)
    st["clients"].add(ws)
    await ws.send_json({"type": "hello", "busy": is_busy(pid)})
    try:
        while True:
            try:
                msg = await ws.receive_json()
            except WebSocketDisconnect:
                raise
            except (ValueError, KeyError, RuntimeError):
                await ws.send_json({"type": "error", "text": "The server couldn't read that message."})
                continue
            if not isinstance(msg, dict):
                await ws.send_json({"type": "error", "text": "The server couldn't read that message."})
                continue
            try:
                await handle_ws_message(ws, pid, st, msg)
            except HTTPException as e:
                await ws.send_json({"type": "error", "text": e.detail})
            except Exception:
                ref = secrets.token_hex(3)
                log.exception("WebSocket message failed [ref %s]", ref)
                await ws.send_json({"type": "error", "text": f"Something went wrong on the server (ref {ref})."})
    except WebSocketDisconnect:
        st["clients"].discard(ws)


async def handle_ws_message(ws: WebSocket, pid: str, st: dict, msg: dict):
    """One message from the studio: prompt, stop, continue, restart or retry."""
    if msg.get("type") == "prompt" and msg.get("text", "").strip():
        if is_busy(pid):
            await ws.send_json({"type": "error", "text": "Agents are already working. Stop them first."})
            return
        details = load_meta(pid)
        if not details.get("description") or details.get("name") == "Untitled project":
            await ws.send_json({"type": "needs_details",
                                "text": "Give this project a name and a description before the agents start."})
            return
        text = msg["text"].strip()
        valid = {a["id"] for a in load_agents()}
        agent_ids = [i for i in msg.get("agents") or [] if i in valid] or default_ids()
        images = [i for i in (msg.get("images") or [])[:6]
                  if isinstance(i, str) and i.startswith("uploads/") and (WORK / pid / i).is_file()
                  and (WORK / pid).resolve() in (WORK / pid / i).resolve().parents]
        await broadcast(pid, {"type": "user", "text": text, "images": images})
        st["task"] = asyncio.create_task(run_chain(pid, text, agent_ids, images=images))
    elif msg.get("type") in ("continue", "restart"):
        mode = msg["type"]
        if is_busy(pid):
            await ws.send_json({"type": "error", "text": "Agents are already working."})
            return
        details = load_meta(pid)
        run = details.get("run") or {}
        summary = run_summary(run)
        if not run.get("request") or run.get("status") == "running":
            await ws.send_json({"type": "error", "text": "There is no stopped run to pick up."})
            return
        if mode == "continue" and not summary["resumable"]:
            await ws.send_json({"type": "error", "text": "This run can't be continued. Start it again instead."})
            return
        if not details.get("description") or details.get("name") == "Untitled project":
            await ws.send_json({"type": "needs_details",
                                "text": "Give this project a name and a description before the agents start."})
            return
        valid = {a["id"] for a in load_agents()}
        agent_ids = [i for i in run.get("agents", []) if i in valid]
        if not agent_ids:
            await ws.send_json({"type": "error", "text": "None of this run's agents exist any more."})
            return
        await broadcast(pid, {"type": "resume", "mode": mode, "request": run["request"],
                              "from": summary["next"] if mode == "continue" else agent_ids[0]})
        st["task"] = asyncio.create_task(run_chain(pid, run["request"], agent_ids, resume=mode == "continue",
                                                   images=run.get("images", [])))
    elif msg.get("type") == "retry":
        if is_busy(pid):
            await ws.send_json({"type": "error", "text": "Agents are already working."})
            return
        details = load_meta(pid)
        agent = next((a for a in load_agents() if a["id"] == msg.get("agent")), None)
        request = (details.get("run") or {}).get("request") or (details.get("history") or [None])[-1]
        if not agent or not request:
            await ws.send_json({"type": "error", "text": "Nothing to retry yet — send a prompt first."})
            return
        note = " ".join(str(msg.get("note") or "").split())[:500]
        st["task"] = asyncio.create_task(run_retry(pid, agent, request, note))
    elif msg.get("type") == "stop" and is_busy(pid):
        st["task"].cancel()
