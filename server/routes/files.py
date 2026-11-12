"""A project's files: the Code tab (list and save), downloads, reference images and brand assets, code versions,
and serving the site (/work/…) and its media (/media/…)."""

import asyncio
import base64
import errno
import io
import os
import re
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from core import storage
from accounts import plans
from site_tools import browser_check
from site_tools import images as photo_copies

from state import (IMAGE_EXT, IMAGE_TYPES, MAX_ASSETS, MAX_EDIT, MAX_IMAGE, TEXT_EXT, VERS, broadcast, check_id,
                   human_size, is_busy, load_meta, log, owner_of, save_meta, site_dir, store, work_dir)
from web import page_of
from project_data import ASSET_KINDS, safe_path

router = APIRouter()


@router.get("/api/projects/{pid}/files")
async def list_files(pid: str):
    """Every file with its size. Text content is included only for files small enough to edit."""
    pid = load_meta(pid)["id"]
    folder = site_dir(pid)
    files = []
    for p in sorted(folder.rglob("*")) if folder.exists() else []:
        if not p.is_file() or p.relative_to(folder).parts[0] == browser_check.CHECK_DIR:
            continue
        size = p.stat().st_size
        item = {"path": str(p.relative_to(folder)), "size": size, "content": None,
                "kind": "image" if p.suffix.lower() in IMAGE_EXT else "text" if p.suffix.lower() in TEXT_EXT else "other"}
        if item["kind"] == "image":
            item["url"] = f"/work/{pid}/{item['path']}"   # the folder is works/<pid>/site, so not folder.name
        elif size > MAX_EDIT:
            item["too_large"] = True
        else:
            try:
                item["content"] = p.read_text()
            except UnicodeDecodeError:
                item["kind"] = "binary"
        files.append(item)
    return files


CREDIT = ('<p style="margin:24px 0;text-align:center;font:13px/1.4 system-ui,sans-serif;opacity:.7">'
          'Made with Agents Atelier</p>')


def with_credit(html: bytes) -> bytes:
    """A small line before </body>, for sites downloaded on the Starter plan."""
    text = html.decode(errors="replace")
    i = text.lower().rfind("</body>")
    return (text[:i] + CREDIT + "\n" + text[i:] if i >= 0 else text + CREDIT).encode()


@router.get("/api/projects/{pid}/download")
async def download(pid: str):
    meta = load_meta(pid)
    credit = plans.of(await owner_of(meta))["credit"]
    if is_busy(meta["id"]):
        raise HTTPException(409, "Agents are still working on this project. Download it when they finish.")
    folder = site_dir(meta["id"])
    buf = io.BytesIO()
    slug = re.sub(r"[^a-z0-9]+", "-", meta["name"].lower()).strip("-")[:40] or "project"
    root = f"{slug}-{meta['id'][:8]}"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file() and p.relative_to(folder).parts[0] != browser_check.CHECK_DIR:   # the server's own screenshots
                if credit and p.suffix.lower() in (".html", ".htm"):
                    z.writestr(f"{root}/{p.relative_to(folder)}", with_credit(p.read_bytes()))
                else:
                    z.write(p, f"{root}/{p.relative_to(folder)}")
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{root}.zip"'})


@router.put("/api/projects/{pid}/files")
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
    if target.is_dir():
        raise HTTPException(400, f"“{body.get('path')}” is a folder, not a file.")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(content)
        os.replace(tmp, target)
    except (NotADirectoryError, IsADirectoryError):
        raise HTTPException(400, "That path goes through a file, not a folder. Pick another name.")
    except OSError as e:
        raise HTTPException(507 if e.errno == errno.ENOSPC else 500, f"Couldn't save {target.name}: {e.strerror or e}.")
    meta = load_meta(pid); save_meta(meta)  # bump "updated"
    store.index_site(pid)
    return {"ok": True, "path": str(target.relative_to(site_dir(pid).resolve())), "saved": time.time()}


@router.post("/api/projects/{pid}/uploads")
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
    name = f"{uuid.uuid4()}.{IMAGE_TYPES[m.group(1)]}"
    folder = storage.uploads_dir(pid)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        (folder / name).write_bytes(raw)
    except OSError as e:
        raise HTTPException(507 if e.errno == errno.ENOSPC else 500, f"Couldn't save the image: {e.strerror or e}.")
    store.index_files(pid, "upload", folder)
    return {"path": f"uploads/{name}", "url": f"/media/{pid}/uploads/{name}", "size": len(raw)}


@router.post("/api/projects/{pid}/assets")
async def upload_asset(pid: str, body: dict):
    """A brand image for the site itself (logo or photo), saved in assets/ so it is versioned and downloaded.
    {name, data: data-URL, kind: logo|photo|image, note?} -> the asset entry."""
    meta = load_meta(pid)
    if is_busy(meta["id"]):
        raise HTTPException(409, "Agents are working on this project. Wait for them to finish, or stop them first.")
    kind = str(body.get("kind") or "image")
    if kind not in ASSET_KINDS:
        raise HTTPException(400, "An asset is a logo, a photo or an image.")
    note = " ".join(str(body.get("note") or "").split())
    if len(note) > 120:
        raise HTTPException(400, "Keep the image's note under 120 characters.")
    assets = list(meta.get("assets") or [])
    if len(assets) >= MAX_ASSETS:
        raise HTTPException(413, f"A project can start with up to {MAX_ASSETS} images.")
    data = str(body.get("data", ""))
    m = re.match(r"^data:(image/[a-z+.-]+);base64,(.+)$", data, re.S)
    if not m or m.group(1) not in IMAGE_TYPES:
        raise HTTPException(400, "Upload a PNG, JPG, WebP or GIF image.")
    if len(m.group(2)) * 3 // 4 > MAX_IMAGE + 4:
        raise HTTPException(413, f"This image is about {human_size(len(m.group(2)) * 3 // 4)}; the limit is {human_size(MAX_IMAGE)}.")
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except ValueError:
        raise HTTPException(400, "The image data is damaged. Try choosing it again.")
    if not raw:
        raise HTTPException(400, "That image is empty.")
    folder = site_dir(meta["id"]) / "assets"
    stem = "logo" if kind == "logo" else (re.sub(r"[^a-z0-9]+", "-", Path(str(body.get("name") or kind)).stem.lower()).strip("-")[:40] or kind)
    ext = IMAGE_TYPES[m.group(1)]
    name, n = f"{stem}.{ext}", 2
    while (folder / name).exists():
        name, n = f"{stem}-{n}.{ext}", n + 1
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(raw)
    except OSError as e:
        raise HTTPException(507 if e.errno == errno.ENOSPC else 500, f"Couldn't save the image: {e.strerror or e}.")
    entry = {"path": f"assets/{name}", "kind": kind, "note": note, "size": len(raw)}
    if kind in photo_copies.KINDS:
        try:
            entry["variants"] = await asyncio.to_thread(photo_copies.make_variants, folder / name)
        except Exception as e:                  # a damaged image is still saved; the agents use the original
            log.warning("Couldn't make WebP copies of %s: %s", name, e)
            entry["variants"] = []
    meta = load_meta(meta["id"])
    meta["assets"] = [*(meta.get("assets") or []), entry]
    save_meta(meta)
    store.index_site(meta["id"])
    return {**entry, "url": f"/work/{meta['id']}/assets/{name}"}


# ---------------------------------------------------------------- versions

def _version_call(fn, *args):
    try:
        return fn(*args)
    except KeyError:
        raise HTTPException(404, "Unknown version")


@router.get("/api/projects/{pid}/versions")
async def list_versions(pid: str, page: int | None = None, per_page: int = 10):
    """Newest first. Without `page`: the whole list. With `page`: one page plus totals."""
    versions = VERS.list(load_meta(pid)["id"])
    return versions if page is None else page_of(versions, page, per_page)


@router.post("/api/projects/{pid}/versions")
async def save_version(pid: str, body: dict | None = None):
    pid = load_meta(pid)["id"]
    if is_busy(pid):
        raise HTTPException(409, "Agents are working. Versions are saved automatically after each agent.")
    label = " ".join(str((body or {}).get("label") or "Saved by you").split())[:80]
    v = VERS.snapshot(pid, label, kind="manual")
    if not v:
        raise HTTPException(400, "Nothing changed since the last version.")
    return v


@router.get("/api/projects/{pid}/versions/{n}/diff")
async def version_diff(pid: str, n: int, against: int | None = None):
    pid = load_meta(pid)["id"]
    _version_call(VERS.get, pid, n)
    if against is not None:
        _version_call(VERS.get, pid, against)
    return _version_call(VERS.diff, pid, n, against)


@router.post("/api/projects/{pid}/versions/{n}/restore")
async def restore_version(pid: str, n: int):
    pid = load_meta(pid)["id"]
    if is_busy(pid):
        raise HTTPException(409, "Agents are working on this project. Restore when they finish.")
    backup = _version_call(VERS.restore, pid, n)
    await broadcast(pid, {"type": "restored", "version": n, "backup": backup["n"] if backup else None})
    return {"ok": True, "backup": backup}


@router.get("/api/projects/{pid}/versions/{n}/download")
async def download_version(pid: str, n: int):
    meta = load_meta(pid)
    _version_call(VERS.get, meta["id"], n)
    folder = VERS.folder(meta["id"], n)
    buf = io.BytesIO()
    slug = re.sub(r"[^a-z0-9]+", "-", meta["name"].lower()).strip("-")[:40] or "project"
    root = f"{slug}-v{n}"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file() and p.relative_to(folder).parts[0] != browser_check.CHECK_DIR:   # the server's own screenshots
                z.write(p, f"{root}/{p.relative_to(folder)}")
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{root}.zip"'})


# HEAD too: the studio asks with HEAD whether a page exists before loading it in the preview.
@router.api_route("/work/{pid}", methods=["GET", "HEAD"])
@router.api_route("/work/{pid}/{path:path}", methods=["GET", "HEAD"])
async def serve_site(request: Request, pid: str, path: str = ""):
    """A project's website (works/<pid>/site/), reached by its unguessable id and always sandboxed (see no_cache)."""
    try:
        pid = str(uuid.UUID(pid))
    except ValueError:
        raise HTTPException(404, "This page or file doesn't exist.")
    if "\x00" in path:
        raise HTTPException(404, "This page or file doesn't exist.")
    root = site_dir(pid).resolve()
    target = (root / path).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(404, "This page or file doesn't exist.")
    if target.is_dir():
        if not request.url.path.endswith("/"):        # so the page's relative links (style.css…) resolve inside it
            return RedirectResponse(f"/work/{pid}/{path}/" if path else f"/work/{pid}/")
        target = target / "index.html"
    if not target.is_file():
        raise HTTPException(404, "This page or file doesn't exist.")
    return FileResponse(target)


@router.get("/media/{pid}/{area}/{path:path}")
async def project_media(pid: str, area: str, path: str):
    """A project's reference images (uploads/) and browser-check screenshots (checks/): only for who can open the project."""
    if area not in ("uploads", "checks") or "\x00" in path:
        raise HTTPException(404, "This page or file doesn't exist.")
    root = (work_dir(check_id(pid)) / area).resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "This page or file doesn't exist.")
    return FileResponse(target, headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})
