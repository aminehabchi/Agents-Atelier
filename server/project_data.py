"""A project's data: its details, brief, pages and brand kit (validation and defaults), creating and removing a
project, its logo, and safe paths inside its site."""

import base64
import re
import time
import uuid
import zipfile
import zlib
from pathlib import Path

from fastapi import HTTPException

from core import config
from core import db
from accounts import collab
from crew import team_files

from state import IMAGE_TYPES, MAX_IMAGE, human_size, load_meta, runs, save_meta, site_dir, store


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


# The questions asked when a project is created (all optional). key -> (label shown to agents, max length)
BRIEF_FIELDS = {
    "site_type": ("Kind of site", 60),
    "audience": ("Who it is for", 200),
    "goal": ("Main goal / call to action", 200),
    "tone": ("Tone and style", 120),
    "colors": ("Brand colours", 120),
    "language": ("Language(s) of the site", 80),
    "sections": ("Must-have content", 600),
    "inspiration": ("Sites or styles they like", 300),
    "avoid": ("Things to avoid", 200),
    "fonts": ("Fonts", 120),
}


def clean_brief(raw) -> dict:
    """The optional project questionnaire, trimmed. Unknown keys and empty answers are dropped."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise HTTPException(400, "The project brief must be a set of answers.")
    out = {}
    for key, (label, limit) in BRIEF_FIELDS.items():
        value = str(raw.get(key) or "").strip()
        value = "\n".join(" ".join(line.split()) for line in value.splitlines() if line.strip())
        if len(value) > limit:
            raise HTTPException(400, f"Keep “{label}” under {limit} characters.")
        if value:
            out[key] = value
    return out


ASSET_KINDS = {"logo": "the brand logo", "photo": "a photo", "image": "an image"}


MAX_PAGES = 12
DEFAULT_PAGES = [{"title": "Home", "file": "index.html"}]


def clean_pages(raw, keep: list[dict] | None = None) -> list[dict]:
    """A site's pages from a list of titles (or {title} dicts). The first page is always index.html.
    Titles that match a page in `keep` keep that page's file (an imported site keeps its file names)."""
    if raw is None:
        return list(DEFAULT_PAGES)
    if not isinstance(raw, list):
        raise HTTPException(400, "Pages must be a list of page names.")
    titles = [" ".join(str((t.get("title") if isinstance(t, dict) else t) or "").split()) for t in raw]
    titles = [t for t in titles if t]
    if not titles:
        raise HTTPException(400, "A site needs at least one page.")
    if len(titles) > MAX_PAGES:
        raise HTTPException(400, f"A site can have up to {MAX_PAGES} pages.")
    pages, used = [], set()
    kept = {p["title"]: p["file"] for p in keep or [] if p.get("file") != "index.html"}
    for i, title in enumerate(titles):
        if len(title) > 40:
            raise HTTPException(400, f"Keep page names under 40 characters (“{title[:20]}…”).")
        if i and kept.get(title) and kept[title] not in used:
            used.add(kept[title])
            pages.append({"title": title, "file": kept[title]})
            continue
        base = "index" if i == 0 else team_files.slugify(title, f"page-{i + 1}")
        if base == "index" and i:
            base = "home"
        name, n = base, 2
        while name in used:
            name, n = f"{base}-{n}", n + 1
        used.add(name)
        pages.append({"title": title, "file": f"{name}.html"})
    return pages


def create_project(name: str, description: str, pages=None, brief=None, owner: str | None = None, agents: list[str] | None = None,
                   brand: dict | None = None) -> dict:
    pid = str(uuid.uuid4())
    site_dir(pid).mkdir(parents=True)
    now = time.time()
    meta = {"id": pid, "name": name, "description": description, "created": now, "updated": now,
            "owner": owner, "history": [], "agents": agents or [], "pages": clean_pages(pages), "brief": clean_brief(brief), "assets": [],
            "brand": brand or dict(EMPTY_BRAND)}
    save_meta(meta)
    return meta


ZIP_ERRORS = (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, EOFError, OSError, ValueError, zlib.error)


def _unzip(read, name: str) -> bytes:
    """One file out of an uploaded .zip, or a 400 when it's damaged, password-protected or uses an unknown compression."""
    try:
        return read()
    except ZIP_ERRORS:
        raise HTTPException(400, f"“{name[:80]}” couldn't be read from the .zip (it may be damaged or password-protected).")


def _page_title(html: bytes, path: str) -> str:
    m = re.search(rb"<title[^>]*>(.*?)</title>", html[:20000], re.I | re.S)
    title = " ".join(m.group(1).decode("utf-8", "replace").split()) if m else ""
    if not title or len(title) > 40:
        title = Path(path).stem.replace("-", " ").replace("_", " ").strip().capitalize() or "Page"
    return title[:40]


def _remove_project_files(pid: str):
    """The project, its chat, versions and files: its rows in Postgres and its folder works/<pid>/."""
    store.delete(pid)
    runs.pop(pid, None)
    collab.forget_project(pid)


# ---------------------------------------------------------------- brand kit (one per project)
# A project's brand kit: its colours, heading and body fonts (meta["brand"]) and its logo (the project asset of kind
# "logo", in assets/ so it is versioned and downloaded). Every agent gets it in <brand_kit>. A kit can be copied from
# another of your projects, or from the account-wide kit of earlier versions ("account").

BRAND_DIR = config.LEGACY_WORK_DIR / ".brand"   # earlier versions kept one kit per account here (still offered as a copy source)
HEX = re.compile(r"^#[0-9a-f]{6}$")
FONT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ]{0,39}$")
MAX_BRAND_COLORS = 6
EMPTY_BRAND = {"colors": [], "heading_font": "", "body_font": ""}


def _brand_logo(user_id) -> Path | None:
    folder = BRAND_DIR / str(user_id)
    return next((p for p in sorted(folder.glob("logo.*")) if p.is_file()), None) if folder.exists() else None


async def brand_kit(user: dict) -> dict:
    """The account-wide kit of earlier versions (read only now)."""
    row = await db.pool.fetchrow("SELECT * FROM brand_kits WHERE user_id = $1", user["id"])
    kit = {"colors": list(row["colors"]) if row else [], "heading_font": row["heading_font"] if row else "",
           "body_font": row["body_font"] if row else "", "logo": None}
    kit["empty"] = not (kit["colors"] or kit["heading_font"] or kit["body_font"] or _brand_logo(user["id"]))
    return kit


def clean_brand(raw) -> dict:
    """Colours like #1a2b3c (up to 6) and Google Fonts names, or a 400 with a readable message."""
    if raw is None:
        return dict(EMPTY_BRAND)
    if not isinstance(raw, dict):
        raise HTTPException(400, "The brand kit must be colours and fonts.")
    raw_colors = raw.get("colors") or []
    if not isinstance(raw_colors, list):
        raise HTTPException(400, f"Use up to {MAX_BRAND_COLORS} colours written like #1a2b3c.")
    colors = [str(c).strip().lower() for c in raw_colors if str(c).strip()]
    if len(colors) > MAX_BRAND_COLORS or not all(HEX.match(c) for c in colors):
        raise HTTPException(400, f"Use up to {MAX_BRAND_COLORS} colours written like #1a2b3c.")
    out = {"colors": list(dict.fromkeys(colors))}
    for key, label in (("heading_font", "heading"), ("body_font", "body")):
        value = " ".join(str(raw.get(key) or "").split())
        if value and not FONT_NAME.match(value):
            raise HTTPException(400, f"Write the {label} font as a font name, like Inter or Playfair Display.")
        out[key] = value
    return out


def _logo_asset(meta: dict) -> dict | None:
    return next((a for a in meta.get("assets") or [] if a.get("kind") == "logo" and (site_dir(meta["id"]) / a["path"]).is_file()), None)


def brand_of(meta: dict) -> dict:
    kit = {**EMPTY_BRAND, **(meta.get("brand") or {})}
    logo = _logo_asset(meta)
    kit["logo"] = f"/work/{meta['id']}/{logo['path']}?v={int((site_dir(meta['id']) / logo['path']).stat().st_mtime)}" if logo else None
    kit["empty"] = not (kit["colors"] or kit["heading_font"] or kit["body_font"] or logo)
    return kit


def _save_logo(meta: dict, data: str, note: str = "") -> dict:
    """Make `data` (an image data-URL) the project's logo, replacing the previous one."""
    m = re.match(r"^data:(image/[a-z+.-]+);base64,(.+)$", str(data or ""), re.S)
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
    return _put_logo_bytes(meta, raw, IMAGE_TYPES[m.group(1)], note)


def _put_logo_bytes(meta: dict, raw: bytes, ext: str, note: str = "") -> dict:
    _drop_logo(meta)
    folder = site_dir(meta["id"]) / "assets"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"logo.{ext}").write_bytes(raw)
    meta = load_meta(meta["id"])
    meta["assets"] = [*(meta.get("assets") or []), {"path": f"assets/logo.{ext}", "kind": "logo", "note": note, "size": len(raw)}]
    save_meta(meta)
    store.index_site(meta["id"])
    return meta


def _drop_logo(meta: dict):
    meta = load_meta(meta["id"])
    for a in [a for a in meta.get("assets") or [] if a.get("kind") == "logo"]:
        (site_dir(meta["id"]) / a["path"]).unlink(missing_ok=True)
    meta["assets"] = [a for a in meta.get("assets") or [] if a.get("kind") != "logo"]
    save_meta(meta)
    store.index_site(meta["id"])


def safe_path(pid: str, rel: str) -> Path:
    folder = site_dir(pid).resolve()
    if "\x00" in rel or len(rel) > 1000:
        raise HTTPException(400, "That file name isn't valid.")
    target = (folder / rel).resolve()
    if folder not in target.parents:
        raise HTTPException(400, "That path is outside the project.")
    return target
