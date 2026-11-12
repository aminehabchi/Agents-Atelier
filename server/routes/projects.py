"""Projects: list, create (also from an existing site), read, update and delete; the brand kit; the people on a
project; the contact details; and the estimate of what a run will cost."""

import base64
import errno
import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from core import db
from core import limits
from accounts import collab
from accounts import keys
from accounts import plans
from crew import market
from crew import orchestrator as orch
from crew.team_files import default_ids
from site_tools import contact

from state import (MAX_SITE_IMPORT, RESUMABLE, VERS, _all_metas, human_size, is_busy, load_meta, notify, owner_of,
                   project_agents, run_summary, safely, save_meta, site_dir, store)
from web import _limited, can_access, current_user
from project_data import (DEFAULT_PAGES, MAX_PAGES, ZIP_ERRORS, _brand_logo, _drop_logo, _logo_asset, _page_title,
                          _put_logo_bytes, _remove_project_files, _save_logo, _unzip, brand_kit, brand_of, clean_brand,
                          clean_brief, clean_details, clean_pages, create_project, safe_path)
from runs.prompts import CONTROL

router = APIRouter()


@router.get("/api/projects")
async def list_projects(request: Request, page: int | None = None, per_page: int = 12, q: str = "", sort: str = "updated", status: str = "all", mine: bool = False):
    """Without `page`: every project (newest activity first), as a list.
    With `page`: one page of results plus totals, after search (`q`), `status` filter and `sort`.
    `mine` leaves out other people's projects, which only admins can see."""
    user = current_user(request)
    projects = all_projects(user, len(await default_ids(user)))
    if mine:
        projects = [p for p in projects if p["owner"] == str(user["id"])]
    if user["role"] == "admin":   # admins see everyone's projects, so show whose each one is
        emails = {str(r["id"]): r["email"] for r in await db.pool.fetch("SELECT id, email FROM users")}
        for p in projects:
            p["owner_email"] = emails.get(p["owner"] or "", "")
    all_prompts = sum(p["prompts"] for p in projects)
    if page is None:
        return projects

    counts = {"all": len(projects), "working": sum(p["busy"] for p in projects),
              "built": sum(p["has_preview"] for p in projects), "empty": sum(not p["has_preview"] for p in projects),
              "shared": sum(p["role"] in collab.ROLES for p in projects)}
    needle = q.strip().lower()
    if needle:
        projects = [p for p in projects if needle in p["name"].lower() or needle in p["description"].lower() or p["id"].startswith(needle)]
    if status == "working":
        projects = [p for p in projects if p["busy"]]
    elif status == "built":
        projects = [p for p in projects if p["has_preview"]]
    elif status == "empty":
        projects = [p for p in projects if not p["has_preview"]]
    elif status == "shared":                    # projects other people invited you to
        projects = [p for p in projects if p["role"] in collab.ROLES]
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
            "prompts": all_prompts}


def all_projects(user: dict, default_count: int) -> list[dict]:
    """The projects `user` can open."""
    projects = []
    for m in store.all():
        role = collab.role_of(user, m)
        if role is None:
            continue
        folder = site_dir(m["id"])
        files = [f for f in folder.rglob("*") if f.is_file()] if folder.exists() else []
        projects.append({"id": m["id"], "owner": m.get("owner"), "name": m["name"], "description": m.get("description", ""), "created": m["created"],
                         "updated": m["updated"], "busy": is_busy(m["id"]),
                         "prompts": len(m.get("history", [])), "agents": len(m.get("agents") or []) or default_count,
                         "files": len(files), "size": sum(f.stat().st_size for f in files),
                         "pages": [p["title"] for p in m.get("pages") or DEFAULT_PAGES],
                         "brief": m.get("brief") or {},
                         "has_preview": (folder / "index.html").exists(), "role": role,
                         "shared": role in collab.ROLES or (role == "owner" and bool(collab.members.get(m["id"]))),
                         "paused": (m.get("run") or {}).get("status") in RESUMABLE and not is_busy(m["id"])})
    return sorted(projects, key=lambda m: m["updated"], reverse=True)


def check_project_limit(user: dict):
    """Starter accounts keep a few projects; deleting one frees a place."""
    most = plans.of(user)["projects"]
    if most is not None and sum(m.get("owner") == str(user["id"]) for m in store.all()) >= most:
        raise plans.PlanError("The Starter plan includes one project. Delete it, or move to Pro for unlimited projects." if most == 1 else
                              f"The Starter plan holds {most} projects. Delete one, or move to Pro for unlimited projects.")


@router.post("/api/projects")
async def new_project(request: Request, body: dict):
    user = current_user(request)
    check_project_limit(user)
    return create_project(**clean_details(body), pages=body.get("pages"), brief=body.get("brief"),
                          owner=str(user["id"]), agents=await default_ids(user), brand=clean_brand(body.get("brand")))


# Starting a project from an existing site: only web pages, styles, scripts, Markdown and assets are kept.
SITE_EXT = {".html", ".htm", ".css", ".js", ".mjs", ".md",
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg", ".ico",
            ".woff", ".woff2", ".ttf", ".otf", ".mp4", ".webm", ".mp3", ".ogg", ".wav"}
SITE_SKIP_DIRS = {"node_modules", "__MACOSX", "__pycache__"}
MAX_SITE_FILES = 500


def _site_path(raw: str) -> str | None:
    """A clean relative path, or None for anything unsafe or hidden (../, absolute, .git, .env…)."""
    parts = [p for p in str(raw).replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or p.startswith(".") or p in SITE_SKIP_DIRS or CONTROL.search(p) for p in parts):
        return None
    return "/".join(parts)


def _site_files(body: dict) -> tuple[list[tuple[str, bytes]], list[str]]:
    """The files sent to start a project, as (path, bytes) plus the names that were left out.
    Accepts {zip: base64} or {files: [{path, data: base64}]}."""
    def b64(value, label):
        try:
            return base64.b64decode(str(value or ""), validate=True)
        except ValueError:
            raise HTTPException(400, f"The data for {label} is damaged. Try choosing the files again.")

    entries = []   # (path, size, read)
    if body.get("zip"):
        try:
            z = zipfile.ZipFile(io.BytesIO(b64(body["zip"], "the .zip")))
        except ZIP_ERRORS:
            raise HTTPException(400, "That isn't a valid .zip file.")
        entries = [(i.filename, i.file_size, (lambda i=i: z.read(i))) for i in z.infolist() if not i.is_dir()]
    elif isinstance(body.get("files"), list):
        for f in body["files"]:
            if not isinstance(f, dict):
                raise HTTPException(400, "Each file needs a path and its data.")
            data = b64(f.get("data"), str(f.get("path") or "a file"))
            entries.append((str(f.get("path") or ""), len(data), (lambda d=data: d)))
    else:
        raise HTTPException(400, "Choose a folder or a .zip with your site.")

    keep, skipped = [], []
    for name, size, read in entries:
        path = _site_path(name)
        if not path or Path(path).suffix.lower() not in SITE_EXT:
            if path or not any(seg in SITE_SKIP_DIRS or seg.startswith(".") for seg in name.split("/")):
                skipped.append(name)
            continue
        keep.append((path, size, read))
    if len(keep) > MAX_SITE_FILES or sum(s for _, s, _ in keep) > MAX_SITE_IMPORT:
        raise HTTPException(413, f"That site is too big to import (limit {MAX_SITE_FILES} files, {human_size(MAX_SITE_IMPORT)}).")
    # A folder or .zip usually wraps everything in one top folder: drop it so index.html sits at the root.
    while keep and all("/" in p for p, _, _ in keep) and len({p.split("/", 1)[0] for p, _, _ in keep}) == 1:
        keep = [(p.split("/", 1)[1], s, r) for p, s, r in keep]
    files = {}
    for path, _, read in keep:
        if path.lower().endswith(".htm"):
            path = path[:-4] + ".html"
        files[path] = _unzip(read, path)
    if not any(p.lower().endswith(".html") for p in files):
        raise HTTPException(400, "No web pages (.html) were found. Choose the folder or .zip that holds your site.")
    if "index.html" not in files:
        raise HTTPException(400, "Your site needs an index.html (the home page) at its top level.")
    return sorted(files.items()), skipped


@router.post("/api/projects/import")
async def import_project(request: Request, body: dict):
    """Create a project that starts from an existing site (HTML, CSS, JS, Markdown and assets only)."""
    check_project_limit(current_user(request))
    details = clean_details(body)
    brief = clean_brief(body.get("brief"))
    files, skipped = _site_files(body)
    html = [p for p, _ in files if p.endswith(".html")]
    html.sort(key=lambda p: (p != "index.html", p.count("/"), p))
    pages, titles = [], set()
    for p in html[:MAX_PAGES]:
        title, n = _page_title(dict(files)[p], p), 2
        base = title
        while title in titles:
            title, n = f"{base} {n}"[:40], n + 1
        titles.add(title)
        pages.append({"title": title, "file": p})
    user = current_user(request)
    meta = create_project(**details, brief=brief, owner=str(user["id"]), agents=await default_ids(user), brand=clean_brand(body.get("brand")))
    folder = site_dir(meta["id"])
    try:
        for path, data in files:
            target = safe_path(meta["id"], path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    except (OSError, HTTPException) as e:
        _remove_project_files(meta["id"])
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(507 if e.errno == errno.ENOSPC else 500, f"Couldn't save the imported files: {e.strerror or e}.")
    meta["pages"] = pages
    meta["imported"] = {"files": len(files), "skipped": len(skipped)}
    save_meta(meta)
    store.index_site(meta["id"])
    safely("Snapshot", VERS.snapshot, meta["id"], "Imported site", kind="manual")
    return {**meta, "skipped": skipped[:50]}


@router.get("/api/projects/{pid}")
async def get_project(request: Request, pid: str):
    meta = load_meta(pid)
    events = await store.events(meta["id"])
    if not meta.get("agents"):
        meta["agents"] = await default_ids(await owner_of(meta))
    meta.setdefault("description", "")
    meta.setdefault("pages", list(DEFAULT_PAGES))
    return {**meta, "busy": is_busy(meta["id"]), "events": events, "run": run_summary(meta.get("run")),
            "my_role": collab.role_of(current_user(request), meta), "people": len(collab.members.get(meta["id"], {}))}


@router.patch("/api/projects/{pid}")
async def update_project(pid: str, body: dict):
    meta = load_meta(pid)
    meta.update(clean_details(body, partial=True))
    if "pages" in body:
        meta["pages"] = clean_pages(body["pages"], keep=meta.get("pages"))
    if "brief" in body:
        meta["brief"] = clean_brief(body["brief"])
    if isinstance(body.get("agents"), list):
        meta["agents"] = [a["id"] for a in await project_agents(meta) if a["id"] in body["agents"]]
    if "showcase" in body:       # the owner lets this site appear in the portfolios of the agents that built it
        meta["showcase"] = bool(body["showcase"])
    save_meta(meta)
    return meta


def _own_project(request: Request, pid: str) -> dict:
    meta = load_meta(pid)
    if collab.role_of(current_user(request), meta) not in ("owner", "editor"):   # not admins: it's the member's brand
        raise HTTPException(403, "Only the project's owner and editors can change its brand kit.")
    return meta


@router.get("/api/projects/{pid}/brand")
async def get_project_brand(pid: str):
    return brand_of(load_meta(pid))


@router.put("/api/projects/{pid}/brand")
async def put_project_brand(request: Request, pid: str, body: dict):
    meta = _own_project(request, pid)
    meta["brand"] = clean_brand(body)
    save_meta(meta)
    return brand_of(meta)


@router.post("/api/projects/{pid}/brand/logo")
async def put_project_logo(request: Request, pid: str, body: dict):
    meta = _own_project(request, pid)
    if is_busy(meta["id"]):
        raise HTTPException(409, "Agents are working on this project. Wait for them to finish, or stop them first.")
    return brand_of(_save_logo(meta, body.get("data"), "From the brand kit"))


@router.delete("/api/projects/{pid}/brand/logo")
async def delete_project_logo(request: Request, pid: str):
    meta = _own_project(request, pid)
    if is_busy(meta["id"]):
        raise HTTPException(409, "Agents are working on this project. Wait for them to finish, or stop them first.")
    _drop_logo(meta)
    return brand_of(load_meta(pid))


@router.get("/api/brand/sources")
async def brand_sources(request: Request):
    """Kits you can copy into a project: your other projects' kits, and your account kit from earlier versions."""
    user = current_user(request)
    out = []
    for m in _all_metas():
        if m.get("owner") == str(user["id"]):
            kit = brand_of(m)
            if not kit["empty"]:
                out.append({"id": m["id"], "name": m["name"], **kit})
    out.sort(key=lambda k: k["name"].lower())
    legacy = await brand_kit(user)
    if not legacy["empty"]:
        out.insert(0, {"id": "account", "name": "Your saved brand kit", **legacy,
                       "logo": "/api/brand/logo" if _brand_logo(user["id"]) else None})
    return out


@router.get("/api/brand/logo")
async def legacy_brand_logo(request: Request):
    logo = _brand_logo(current_user(request)["id"])
    if not logo:
        raise HTTPException(404, "There's no logo in your brand kit.")
    return FileResponse(logo, headers={"Cache-Control": "private, no-cache"})


@router.post("/api/projects/{pid}/brand/copy")
async def copy_project_brand(request: Request, pid: str, body: dict):
    """Copy another kit into this project: colours and fonts (unless `logo_only`), and the logo when it has one."""
    meta = _own_project(request, pid)
    user = current_user(request)
    source = str(body.get("from") or "")
    if source == "account":
        kit, logo = await brand_kit(user), _brand_logo(user["id"])
        logo_bytes, ext = (logo.read_bytes(), logo.suffix.lstrip(".")) if logo else (None, "")
    else:
        try:
            other = load_meta(source)
        except HTTPException:
            raise HTTPException(404, "That project doesn't exist any more.")
        if other.get("owner") != str(user["id"]):
            if not can_access(user, other):         # someone else's project: as if it didn't exist
                raise HTTPException(404, "That project doesn't exist any more.")
            raise HTTPException(403, "You can only copy the brand kit of your own projects.")
        kit, asset = brand_of(other), _logo_asset(other)
        logo_bytes, ext = ((site_dir(other["id"]) / asset["path"]).read_bytes(), Path(asset["path"]).suffix.lstrip(".")) if asset else (None, "")
    if not body.get("logo_only"):
        meta["brand"] = clean_brand(kit)
        save_meta(meta)
    if logo_bytes and not (body.get("logo_only") and _logo_asset(meta)):
        if is_busy(meta["id"]):
            raise HTTPException(409, "Agents are working on this project. Wait for them to finish, or stop them first.")
        _put_logo_bytes(load_meta(pid), logo_bytes, ext, "From the brand kit")
    return brand_of(load_meta(pid))


@router.delete("/api/projects/{pid}")
async def delete_project(request: Request, pid: str):
    meta = load_meta(pid)
    if not collab.can_manage(current_user(request), meta):
        raise HTTPException(403, "Only the project's owner can delete it.")
    if is_busy(meta["id"]):
        raise HTTPException(409, "Agents are working on this project. Stop them first.")
    _remove_project_files(meta["id"])
    return {"ok": True}


# ---------------------------------------------------------------- editing & uploads

# ---------------------------------------------------------------- people on a project (see collab.py)

def _managed(request: Request, pid: str) -> dict:
    meta = load_meta(pid)
    if not collab.can_manage(current_user(request), meta):
        raise HTTPException(403, "Only the project's owner can choose who works on it.")
    return meta


# ---------------------------------------------------------------- what a run will probably cost

ESTIMATE_RECENT = 5          # this project's last runs of an agent weigh in first


@router.get("/api/projects/{pid}/estimate")
async def run_estimate(pid: str, agents: str = ""):
    """About how much a run of these agents (default: the project's team) will cost on the owner's Claude account, and how
    long it takes. Per agent: its recent runs on this project, else its runs everywhere (market.agent_estimate), else a
    default for its model. A follow-up usually runs fewer agents (the Orchestrator picks), so it's an upper estimate."""
    meta = load_meta(pid)
    owner = await owner_of(meta)
    visible = await project_agents(meta)
    by_id = {a["id"]: a for a in visible}
    wanted = [i for i in agents.split(",") if i in by_id] if agents else [i for i in (meta.get("agents") or []) if i in by_id]
    wanted = wanted or await default_ids(owner)
    ids = [a["id"] for a in visible if a["id"] in set(wanted) | {orch.BRIEF}]       # Brief always runs; team order
    rows = await market._agent_rows()
    stats = market.run_stats(_all_metas(), rows)
    oid = owner["id"] if owner else None
    here = [e for e in meta.get("stats") or [] if e.get("status") == "done"]
    pages = max(1, len(meta.get("pages") or []))
    first = orch.is_fresh(meta, site_dir(pid))
    out = []
    for i in ids:
        mine = [e for e in here if e.get("agent") == i][-ESTIMATE_RECENT:]
        if mine:
            cost = sum(float(e.get("cost") or 0) for e in mine) / len(mine)
            seconds = sum(float(e.get("seconds") or 0) for e in mine) / len(mine)
            source = "project"
        else:
            row = next((r for r in rows if r["slug"] == i and r["owner_id"] == oid), None) or \
                  next((r for r in rows if r["slug"] == i and r["owner_id"] is None), None)
            if row is not None:
                seconds, cost, measured = market.agent_estimate(row, stats, rows)
                source = "measured" if measured else "default"
            else:
                seconds, cost, source = market.DEFAULT_SECONDS, market._default_cost(by_id[i].get("model") or ""), "default"
            if first:                                # a first build writes every page from scratch
                scale = 1 + 0.25 * (pages - 1)
                seconds, cost = seconds * scale, cost * scale
        out.append({"id": i, "name": by_id[i]["name"], "cost": round(cost, 4), "seconds": round(seconds, 1), "source": source})
    total = sum(a["cost"] for a in out)
    limit = plans.run_limit(owner) if keys.USER_MODE else 0
    return {"agents": out, "cost": round(total, 2), "low": round(total * 0.7, 2), "high": round(total * 1.3, 2),
            "seconds": round(sum(a["seconds"] for a in out)), "first_build": first, "limit": limit,
            "over_limit": bool(limit) and total > limit,
            "measured": sum(a["source"] != "default" for a in out), "count": len(out)}


@router.get("/api/projects/{pid}/people")
async def project_people(request: Request, pid: str):
    meta = load_meta(pid)
    return {**await collab.list_people(meta), "my_role": collab.role_of(current_user(request), meta),
            "can_invite": plans.of(await owner_of(meta)).get("members", False)}


@router.post("/api/projects/{pid}/people")
async def invite_person(request: Request, pid: str, body: dict):
    meta = _managed(request, pid)
    _limited(limits.INVITES, request, "invitations", per_member=True)
    out = await collab.invite(meta, current_user(request), body.get("email"), body.get("role") or "editor")
    await notify(meta["id"], {"type": "people"})
    return out


@router.patch("/api/projects/{pid}/people/{user_id}")
async def change_person(request: Request, pid: str, user_id: str, body: dict):
    meta = _managed(request, pid)
    out = await collab.set_role(meta, user_id, body.get("role"))
    await notify(meta["id"], {"type": "people"})
    return out


@router.delete("/api/projects/{pid}/people/{user_id}")
async def remove_person(request: Request, pid: str, user_id: str):
    meta = _managed(request, pid)
    await collab.remove(meta, user_id)
    await notify(meta["id"], {"type": "people"})
    return {"ok": True}


@router.delete("/api/projects/{pid}/membership")
async def leave_project(request: Request, pid: str):
    """An editor or viewer takes themselves off a project."""
    meta, user = load_meta(pid), current_user(request)
    if collab.role_of(user, meta) not in collab.ROLES:
        raise HTTPException(400, "You own this project (or you're an admin): there's nothing to leave.")
    await collab.remove(meta, user["id"])
    await notify(meta["id"], {"type": "people"})
    return {"ok": True}


@router.delete("/api/projects/{pid}/invites")
async def cancel_invite(request: Request, pid: str, email: str = ""):
    meta = _managed(request, pid)
    await collab.cancel_invite(meta, email)
    return {"ok": True}


@router.put("/api/projects/{pid}/contact")
async def put_contact(pid: str, body: dict):
    """The contact details the contact agents use: {whatsapp, phone, email, telegram}. An empty one is "not given":
    its button stays hidden and it isn't asked again. Applies from the next request."""
    meta = load_meta(pid)
    found = {}
    for key in contact.FIELDS:
        if key in body:
            value = str(body.get(key) or "").strip()
            try:
                found[key] = contact.clean(key, value) if value else ""
            except ValueError as e:
                raise HTTPException(400, str(e))
    saved, skipped = dict(meta.get("contact") or {}), list(meta.get("contact_skipped") or [])
    for key, value in found.items():
        if value:
            saved[key] = value
            skipped = [k for k in skipped if k != key]
        else:
            saved.pop(key, None)
            skipped = skipped if key in skipped else [*skipped, key]
    meta["contact"], meta["contact_skipped"] = saved, skipped
    save_meta(meta)
    return {"contact": saved, "contact_skipped": skipped}
