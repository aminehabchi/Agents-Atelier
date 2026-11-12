"""Import the folder of earlier versions (ATELIER_WORK, default work/) into STORAGE_DIR and Postgres.

Earlier versions kept everything in one folder:
  work/<pid>/                  the site, with uploads/ (reference images) and .checks/ (browser-check screenshots)
  work/.projects/<pid>.json    the project's data, its requests and its agent runs
  work/.projects/<pid>.events.jsonl   the chat
  work/.versions/<pid>/index.json + NNNN/   the saved versions

On startup, every project that isn't in the database yet is copied over: files into works/<pid>/ (site/, uploads/,
checks/legacy/, versions/<uuid>/), data into Postgres. The old folder is never changed, so it can be deleted by hand
once the import looks right. Running it again skips the projects already imported.
"""

import json
import logging
import shutil
import uuid
from pathlib import Path

from core import storage

log = logging.getLogger("agents-atelier.legacy")
NOT_SITE = {"uploads", ".checks"}


def _copy_site(src: Path, dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    if not src.is_dir():
        return
    for p in src.iterdir():
        if p.name in NOT_SITE:
            continue
        if p.is_dir():
            shutil.copytree(p, dest / p.name, dirs_exist_ok=True)
        elif p.is_file():
            shutil.copy2(p, dest / p.name)


def import_work_folder(old: Path, store: "storage.Store") -> int:
    meta_dir = old / ".projects"
    if not meta_dir.is_dir() or old.resolve() == storage.ROOT.resolve():
        return 0
    count = 0
    for f in sorted(meta_dir.glob("*.json")):
        try:
            meta = json.loads(f.read_text())
            pid = str(uuid.UUID(str(meta["id"])))
            meta["name"]
        except (ValueError, KeyError, TypeError, OSError):
            log.warning("Skipping %s: not a readable project file", f)
            continue
        if store.get(pid) is not None:
            continue
        src = old / pid
        _copy_site(src, storage.site_dir(pid))
        if (src / "uploads").is_dir():
            shutil.copytree(src / "uploads", storage.uploads_dir(pid), dirs_exist_ok=True)
        if (src / ".checks").is_dir():
            shutil.copytree(src / ".checks", storage.checks_dir(pid) / "legacy", dirs_exist_ok=True)

        stats = meta.pop("stats", []) or []
        meta["id"] = pid
        for key, default in (("description", ""), ("history", []), ("agents", []), ("pages", [{"title": "Home", "file": "index.html"}]),
                             ("brief", {}), ("assets", []), ("brand", {}), ("run", {})):
            meta.setdefault(key, default)
        meta.setdefault("created", f.stat().st_mtime)
        meta.setdefault("updated", meta["created"])
        store.put(meta)
        for entry in stats:
            if isinstance(entry, dict) and entry.get("agent") and entry.get("ts"):
                store.add_stat(pid, entry)

        events = meta_dir / f"{pid}.events.jsonl"
        if events.is_file():
            last = 0
            for line in events.read_text().splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and isinstance(event.get("seq"), int) and event.get("type"):
                    store.add_event(pid, event)
                    last = max(last, event["seq"])
            store.seq[pid] = last

        vroot = old / ".versions" / pid
        try:
            index = json.loads((vroot / "index.json").read_text()) if (vroot / "index.json").is_file() else []
        except ValueError:
            index = []
        for v in index:
            folder = vroot / f"{v.get('n', 0):04d}"
            if not folder.is_dir():
                continue
            vid = str(uuid.uuid4())
            shutil.copytree(folder, storage.versions_dir(pid) / vid)
            store.add_version(pid, {"id": vid, "n": v["n"], "label": v.get("label", ""), "kind": v.get("kind", "manual"),
                                    "agent": v.get("agent"), "request": v.get("request", ""), "ts": v.get("ts", 0),
                                    "files": v.get("files", 0), "size": v.get("size", 0), "hash": v.get("hash", "")})
            store.index_files(pid, "version", storage.versions_dir(pid) / vid, version_id=vid)
        store.index_site(pid)
        store.index_files(pid, "upload", storage.uploads_dir(pid))
        store.index_files(pid, "check", storage.checks_dir(pid))
        count += 1
    return count
