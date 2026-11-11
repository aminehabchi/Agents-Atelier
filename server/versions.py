"""Code versioning: snapshots of a project's site.

works/<pid>/versions/<version-uuid>/...   a full copy of works/<pid>/site/ at that moment
Postgres `versions`                       one row per version: number, label, kind, agent, size, hash
Postgres `files` (area 'version')         every file of every version, with its own uuid

Snapshots are taken automatically before a run and after every agent, and on demand.
Identical consecutive snapshots are skipped.
"""

from __future__ import annotations   # the class has a method named `list`

import difflib
import hashlib
import shutil
import time
import uuid
from pathlib import Path

import config
import storage

MAX_VERSIONS = config.MAX_VERSIONS
SKIP = {"uploads", ".checks"}          # never part of the code (only sites from older versions still have them)


def _files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(p for p in folder.rglob("*")
                  if p.is_file() and p.relative_to(folder).parts[0] not in SKIP)


def _hash(folder: Path) -> str:
    h = hashlib.sha1()
    for p in _files(folder):
        h.update(str(p.relative_to(folder)).encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


class Versions:
    def __init__(self, store: "storage.Store"):
        self.store = store
        self.keep = lambda pid: None      # versions kept for this project (None: MAX_VERSIONS); main.py sets it

    def _index(self, pid: str) -> list[dict]:
        return self.store.version_list(pid)

    def list(self, pid: str) -> list[dict]:
        return list(reversed(self._index(pid)))

    def get(self, pid: str, n: int) -> dict:
        v = next((v for v in self._index(pid) if v["n"] == n), None)
        if not v:
            raise KeyError(n)
        return v

    def folder(self, pid: str, n: int) -> Path:
        return storage.versions_dir(pid) / self.get(pid, n)["id"]

    def snapshot(self, pid: str, label: str, kind: str = "manual", agent: str | None = None,
                 request: str = "", force: bool = False) -> dict | None:
        """Copy the site into a new version. Returns None if nothing changed since the last one."""
        site = storage.site_dir(pid)
        files = _files(site)
        if not files:
            return None
        digest = _hash(site)
        items = self._index(pid)
        if items and items[-1]["hash"] == digest and not force:
            return None
        vid = str(uuid.uuid4())
        dest = storage.versions_dir(pid) / vid
        for p in files:
            target = dest / p.relative_to(site)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
        v = {"id": vid, "n": (items[-1]["n"] + 1) if items else 1, "label": label, "kind": kind, "agent": agent,
             "request": request[:140], "ts": time.time(), "files": len(files), "size": sum(p.stat().st_size for p in files), "hash": digest}
        self.store.add_version(pid, v)
        self.store.index_files(pid, "version", dest, version_id=vid)
        items.append(v)
        keep = min(self.keep(pid) or MAX_VERSIONS, MAX_VERSIONS)   # the owner's plan may keep fewer
        while len(items) > keep:                              # prune the oldest
            old = items.pop(0)
            shutil.rmtree(storage.versions_dir(pid) / old["id"], ignore_errors=True)
            self.store.drop_version(pid, old["id"])
        return v

    def read_files(self, pid: str, n: int | None) -> dict[str, str]:
        """{path: text} for a version, or for the current site when n is None."""
        base = storage.site_dir(pid) if n is None else self.folder(pid, n)
        out = {}
        for p in _files(base):
            try:
                out[str(p.relative_to(base))] = p.read_text()
            except UnicodeDecodeError:
                out[str(p.relative_to(base))] = "(binary file)"
        return out

    def diff(self, pid: str, n: int, against: int | None = None) -> list[dict]:
        """Per-file unified diff from version n to `against` (a version, or the current files when None)."""
        old, new = self.read_files(pid, n), self.read_files(pid, against)
        out = []
        for path in sorted(set(old) | set(new)):
            a, b = old.get(path), new.get(path)
            if a == b:
                continue
            lines = list(difflib.unified_diff((a or "").splitlines(), (b or "").splitlines(),
                                              f"v{n}/{path}", f"{'current' if against is None else 'v' + str(against)}/{path}",
                                              lineterm="", n=3))
            out.append({"path": path, "status": "added" if a is None else "removed" if b is None else "changed",
                        "added": sum(1 for l in lines if l.startswith("+") and not l.startswith("+++")),
                        "removed": sum(1 for l in lines if l.startswith("-") and not l.startswith("---")),
                        "diff": "\n".join(lines[2:])})
        return out

    def restore(self, pid: str, n: int) -> dict | None:
        """Put version n back as the current site. The current state is saved first."""
        src = self.folder(pid, n)
        backup = self.snapshot(pid, f"Before restoring v{n}", kind="backup")
        site = storage.site_dir(pid)
        for p in _files(site):
            p.unlink()
        for d in sorted((d for d in site.rglob("*") if d.is_dir()), reverse=True):
            if d.relative_to(site).parts[0] not in SKIP and not any(d.iterdir()):
                d.rmdir()
        for p in _files(src):
            target = site / p.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
        self.store.index_site(pid)
        return backup
