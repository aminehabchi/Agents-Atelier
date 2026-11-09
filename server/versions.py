"""Code versioning: snapshots of a project's site folder.

work/.versions/<pid>/index.json      list of versions (newest last)
work/.versions/<pid>/0007/...        a full copy of the site at that moment

Snapshots are taken automatically before a run and after every agent, and on demand.
Identical consecutive snapshots are skipped. uploads/ (reference images) is never versioned.
"""

import difflib
import hashlib
import json
import shutil
import time
from pathlib import Path

import config

MAX_VERSIONS = config.MAX_VERSIONS
SKIP = {"uploads", ".checks"}          # top-level folders that are not part of the code


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
    def __init__(self, work: Path):
        self.work = work
        self.root = work / ".versions"

    def _dir(self, pid: str) -> Path:
        return self.root / pid

    def _index(self, pid: str) -> list[dict]:
        f = self._dir(pid) / "index.json"
        return json.loads(f.read_text()) if f.exists() else []

    def _save_index(self, pid: str, items: list[dict]):
        self._dir(pid).mkdir(parents=True, exist_ok=True)
        (self._dir(pid) / "index.json").write_text(json.dumps(items, indent=1))

    def list(self, pid: str) -> list[dict]:
        return list(reversed(self._index(pid)))

    def get(self, pid: str, n: int) -> dict:
        v = next((v for v in self._index(pid) if v["n"] == n), None)
        if not v:
            raise KeyError(n)
        return v

    def folder(self, pid: str, n: int) -> Path:
        return self._dir(pid) / f"{n:04d}"

    def snapshot(self, pid: str, label: str, kind: str = "manual", agent: str | None = None,
                 request: str = "", force: bool = False) -> dict | None:
        """Copy the site folder into a new version. Returns None if nothing changed since the last one."""
        site = self.work / pid
        files = _files(site)
        if not files:
            return None
        digest = _hash(site)
        items = self._index(pid)
        if items and items[-1]["hash"] == digest and not force:
            return None
        n = (items[-1]["n"] + 1) if items else 1
        dest = self.folder(pid, n)
        for p in files:
            target = dest / p.relative_to(site)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
        v = {"n": n, "label": label, "kind": kind, "agent": agent, "request": request[:140], "ts": time.time(),
             "files": len(files), "size": sum(p.stat().st_size for p in files), "hash": digest}
        items.append(v)
        while len(items) > MAX_VERSIONS:                      # prune the oldest
            old = items.pop(0)
            shutil.rmtree(self.folder(pid, old["n"]), ignore_errors=True)
        self._save_index(pid, items)
        return v

    def read_files(self, pid: str, n: int | None) -> dict[str, str]:
        """{path: text} for a version, or for the current site when n is None."""
        base = self.work / pid if n is None else self.folder(pid, n)
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
        self.get(pid, n)
        backup = self.snapshot(pid, f"Before restoring v{n}", kind="backup")
        site = self.work / pid
        for p in _files(site):
            p.unlink()
        for d in sorted((d for d in site.rglob("*") if d.is_dir()), reverse=True):
            if d.relative_to(site).parts[0] not in SKIP and not any(d.iterdir()):
                d.rmdir()
        src = self.folder(pid, n)
        for p in _files(src):
            target = site / p.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
        return backup

    def delete_all(self, pid: str):
        shutil.rmtree(self._dir(pid), ignore_errors=True)
