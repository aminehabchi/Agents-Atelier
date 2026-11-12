"""Share links and pinned comments: managing them on a project, and the public pages a link opens (/s/…)."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from core import limits
from core import storage
from accounts import collab
from accounts import share
from site_tools import browser_check

from state import VERS, is_busy, load_meta, notify, site_dir, store
from web import STATIC, _error_page, _limited, current_user
from project_data import DEFAULT_PAGES

router = APIRouter()


# ---------------------------------------------------------------- share links and pinned comments (see share.py)

def _editable(request: Request, pid: str) -> dict:
    meta = load_meta(pid)
    if not collab.can_edit(current_user(request), meta):
        raise HTTPException(403, "Only the project's owner and editors can do this.")
    return meta


@router.get("/api/projects/{pid}/share")
async def list_share_links(request: Request, pid: str):
    return await share.links(_editable(request, pid)["id"])


@router.post("/api/projects/{pid}/share")
async def create_share_link(request: Request, pid: str, body: dict):
    meta = _editable(request, pid)
    _limited(limits.SHARE_LINKS, request, "share links", per_member=True)
    return await share.create(meta["id"], current_user(request), body, _share_version(meta, body.get("version")))


def _share_version(meta: dict, choice) -> dict | None:
    """What a new share link shows: None (the live site), "now" (the site as it is, saved as a version), or a version number."""
    pid = meta["id"]
    if choice in (None, "", "live"):
        return None
    if choice == "now":
        if is_busy(pid):
            raise HTTPException(409, "Agents are working, so the site may be half-finished. Pick a saved version, or wait until they finish.")
        v = VERS.snapshot(pid, "Shared with a link", kind="share")
        if v is None:                               # nothing changed since the last version: share that one
            versions = VERS.list(pid)
            if not versions:
                raise HTTPException(400, "There's nothing to share yet: the site has no files.")
            v = versions[0]
        return {"id": v["id"], "n": v["n"]}
    try:
        v = VERS.get(pid, int(choice))
    except (KeyError, TypeError, ValueError):
        raise HTTPException(404, "That version doesn't exist any more. Pick another one.")
    return {"id": v["id"], "n": v["n"]}


def _share_root(link: dict, meta: dict) -> Path:
    """The folder a share link shows: its frozen version, or the live site."""
    if link.get("version"):
        root = storage.versions_dir(meta["id"]) / link["version"]
        if not root.is_dir():
            raise HTTPException(404, "The version this link shows is no longer available. Ask the team for a new link.")
        return root
    return site_dir(meta["id"])


@router.patch("/api/projects/{pid}/share/{link_id}")
async def update_share_link(request: Request, pid: str, link_id: str, body: dict):
    """{label?, days?, comments?}: rename a link, give it a new end date (days from now, 0 = never), turn comments on or off."""
    return await share.update_link(_editable(request, pid)["id"], link_id, body)


@router.post("/api/projects/{pid}/comments/{comment_id}/reply")
async def reply_to_comment(request: Request, pid: str, comment_id: str, body: dict):
    """{text}: the team's answer, shown to the visitor under their comment (empty text removes it)."""
    meta = _editable(request, pid)
    out = await share.reply(meta["id"], comment_id, current_user(request), body.get("text"))
    await notify(meta["id"], {"type": "comment", "comment": out})
    return out


@router.delete("/api/projects/{pid}/share/{link_id}")
async def revoke_share_link(request: Request, pid: str, link_id: str):
    await share.revoke(_editable(request, pid)["id"], link_id)
    return {"ok": True}


@router.get("/api/projects/{pid}/comments")
async def list_comments(pid: str):
    return await share.comments(load_meta(pid)["id"])


@router.patch("/api/projects/{pid}/comments/{comment_id}")
async def resolve_comment(request: Request, pid: str, comment_id: str, body: dict):
    meta = _editable(request, pid)
    out = await share.resolve(meta["id"], comment_id, current_user(request), bool(body.get("resolved", True)))
    await notify(meta["id"], {"type": "comment", "comment": out})
    return out


@router.delete("/api/projects/{pid}/comments/{comment_id}")
async def delete_comment(request: Request, pid: str, comment_id: str):
    meta = _editable(request, pid)
    await share.delete_comment(meta["id"], comment_id)
    await notify(meta["id"], {"type": "comment", "deleted": comment_id})
    return {"ok": True}


SHARE_GONE = ("This link has expired or was turned off. Ask the person who sent it for a new one.")


async def _share_link(token: str) -> tuple[dict, dict]:
    link = await share.open_link(token)
    meta = store.get(link["project"]) if link else None
    if not link or not meta:
        raise HTTPException(404, SHARE_GONE)
    return link, meta


@router.get("/s/{token}")
async def share_page(token: str):
    try:
        link, _meta = await _share_link(token)
    except HTTPException:
        return _error_page(404, "Link not available", SHARE_GONE)
    await share.viewed(link)
    return FileResponse(STATIC / "share.html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                                        "X-Robots-Tag": "noindex"})


@router.get("/s/{token}/info")
async def share_info(token: str):
    link, meta = await _share_link(token)
    root = _share_root(link, meta)
    pages = [p for p in meta.get("pages") or DEFAULT_PAGES if (root / p["file"]).is_file()]
    frozen = None
    if link.get("version"):
        v = next((v for v in VERS.list(meta["id"]) if v["id"] == link["version"]), None)
        frozen = {"n": link.get("version_n"), "ts": v["ts"] if v else None}
    return {"name": meta["name"], "pages": pages, "comments": link["comments"], "expires": link["expires"],
            "busy": is_busy(meta["id"]) and not frozen, "built": (root / "index.html").is_file(), "frozen": frozen}


@router.get("/s/{token}/comments")
async def share_comments(token: str):
    link, _meta = await _share_link(token)
    return await share.link_comments(link)


@router.post("/s/{token}/comments")
async def share_add_comment(request: Request, token: str, body: dict):
    link, meta = await _share_link(token)
    _limited(limits.COMMENTS, request, "comments")
    pages = [p["file"] for p in meta.get("pages") or DEFAULT_PAGES]
    comment = await share.add_comment(link, body, pages)
    await notify(meta["id"], {"type": "comment", "comment": comment, "new": True})
    return comment


PIN_SCRIPT = b'<script src="/static/share-pin.js"></script>'


@router.api_route("/s/{token}/site", methods=["GET", "HEAD"])
@router.api_route("/s/{token}/site/{path:path}", methods=["GET", "HEAD"])
async def share_site(request: Request, token: str, path: str = ""):
    """The project's site through a share link: sandboxed like /work/<pid>/ (see no_cache), web pages with the pin script."""
    link, meta = await _share_link(token)
    if "\x00" in path:
        raise HTTPException(404, "This page or file doesn't exist.")
    root = _share_root(link, meta).resolve()
    target = (root / path).resolve()
    if (target != root and root not in target.parents) or (path.split("/")[0] == browser_check.CHECK_DIR):
        raise HTTPException(404, "This page or file doesn't exist.")
    if target.is_dir():
        if not request.url.path.endswith("/"):
            return RedirectResponse(f"/s/{token}/site/{path}/" if path else f"/s/{token}/site/")
        target = target / "index.html"
    if not target.is_file():
        raise HTTPException(404, "This page or file doesn't exist.")
    if target.suffix.lower() in (".html", ".htm"):
        html = target.read_bytes()
        i = html.lower().rfind(b"</body>")
        html = html[:i] + PIN_SCRIPT + html[i:] if i >= 0 else html + PIN_SCRIPT
        return HTMLResponse(html, headers={"Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"})
    return FileResponse(target, headers={"Referrer-Policy": "no-referrer"})
