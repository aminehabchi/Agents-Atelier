"""Where everything is kept.

Files, under STORAGE_DIR (default storage/):

  agents/<agent-uuid>/instructions.md          an agent's instructions
  works/<project-uuid>/site/                   the website the agents edit (served at /work/<project-uuid>/)
  works/<project-uuid>/versions/<version-uuid>/  a full copy of site/ at one moment
  works/<project-uuid>/uploads/<file-uuid>.png   reference images attached to prompts
  works/<project-uuid>/checks/<check-uuid>/      the screenshots of one browser check

Everything else is in Postgres (migration 0006): projects, project_requests, agent_runs, project_events, versions,
and `files`, where every file above has a row with its own uuid, size and SHA-256.

The server's code is mostly synchronous (the run loop, the routes that read a project), so the project data is
kept in memory, loaded from Postgres at startup, and every change is written to Postgres through one ordered queue.
flush() waits until everything queued is saved; reads that go to Postgres directly (the chat) flush first.
"""

import asyncio
import copy
import hashlib
import json
import logging
import shutil
import time
import uuid
from pathlib import Path

import config

log = logging.getLogger("agents-atelier.storage")

ROOT = config.STORAGE_DIR
AGENTS = ROOT / "agents"
WORKS = ROOT / "works"
COLUMNS = ("name", "description", "pages", "brief", "brand", "assets", "agents", "showcase", "run")
STAT_COLUMNS = ("agent", "status", "ts", "run", "request", "seconds", "cost", "tokens_in", "tokens_out", "model", "score")
MAX_STATS = 500                           # agent runs kept in memory per project (all of them stay in Postgres)


def work_dir(pid: str) -> Path:
    return WORKS / pid


def site_dir(pid: str) -> Path:
    return WORKS / pid / "site"


def versions_dir(pid: str) -> Path:
    return WORKS / pid / "versions"


def uploads_dir(pid: str) -> Path:
    return WORKS / pid / "uploads"


def checks_dir(pid: str) -> Path:
    return WORKS / pid / "checks"


def agent_dir(agent_uuid) -> Path:
    return AGENTS / str(agent_uuid)


def _jsonb(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _hash_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Store:
    def __init__(self):
        self.pool = None
        self.projects: dict[str, dict] = {}      # id -> project data, as the rest of the server knows it
        self.versions: dict[str, list[dict]] = {}
        self.seq: dict[str, int] = {}            # last chat event number per project
        self.queue: asyncio.Queue | None = None
        self.task: asyncio.Task | None = None

    # ------------------------------------------------------------ start, stop, writing

    async def start(self, pool):
        self.pool = pool
        ROOT.mkdir(parents=True, exist_ok=True)
        AGENTS.mkdir(exist_ok=True)
        WORKS.mkdir(exist_ok=True)
        self.projects, self.versions, self.seq = {}, {}, {}
        async with pool.acquire() as con:
            for r in await con.fetch("SELECT * FROM projects"):
                self.projects[str(r["id"])] = self._from_row(r)
            for r in await con.fetch("SELECT project_id, text FROM project_requests ORDER BY project_id, n"):
                if str(r["project_id"]) in self.projects:
                    self.projects[str(r["project_id"])]["history"].append(r["text"])
            for r in await con.fetch(f"""SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY project_id ORDER BY ts DESC) AS k
                                                       FROM agent_runs) x WHERE k <= {MAX_STATS} ORDER BY ts"""):
                if str(r["project_id"]) in self.projects:
                    self.projects[str(r["project_id"])]["stats"].append(self._stat_from_row(r))
            for r in await con.fetch("SELECT * FROM versions ORDER BY project_id, n"):
                self.versions.setdefault(str(r["project_id"]), []).append(
                    {"id": str(r["id"]), "n": r["n"], "label": r["label"], "kind": r["kind"], "agent": r["agent"], "request": r["request"],
                     "ts": r["ts"], "files": r["files"], "size": r["size"], "hash": r["hash"]})
            for r in await con.fetch("SELECT project_id, max(seq) AS seq FROM project_events GROUP BY project_id"):
                self.seq[str(r["project_id"])] = r["seq"]
        self.queue = asyncio.Queue()
        self.task = asyncio.create_task(self._writer())

    async def stop(self):
        if self.task:
            await self.flush()
            self.task.cancel()
            self.task = None

    async def flush(self):
        if self.queue is not None:
            await self.queue.join()

    def _write(self, job):
        """Queue `job(con)` (an async function). Jobs run one after another, in the order they were queued."""
        if self.queue is None:
            raise RuntimeError("The store isn't started.")
        self.queue.put_nowait(job)

    async def _writer(self):
        while True:
            job = await self.queue.get()
            try:
                async with self.pool.acquire() as con:
                    await job(con)
            except Exception:
                log.exception("Couldn't save to the database")
            finally:
                self.queue.task_done()

    # ------------------------------------------------------------ projects

    @staticmethod
    def _from_row(r) -> dict:
        meta = {"id": str(r["id"]), "owner": str(r["owner_id"]) if r["owner_id"] else None, "name": r["name"],
                "description": r["description"], "pages": json.loads(r["pages"]), "brief": json.loads(r["brief"]),
                "brand": json.loads(r["brand"]), "assets": json.loads(r["assets"]), "agents": list(r["agents"]),
                "showcase": r["showcase"], "run": json.loads(r["run"]), "created": r["created"], "updated": r["updated"],
                "history": [], "stats": []}
        meta.update(json.loads(r["extra"]))
        return meta

    def ids(self) -> list[str]:
        return list(self.projects)

    def get(self, pid: str) -> dict | None:
        meta = self.projects.get(pid)
        return copy.deepcopy(meta) if meta is not None else None

    def all(self) -> list[dict]:
        return [copy.deepcopy(m) for m in self.projects.values()]

    def put(self, meta: dict):
        """Save a project. Its agent runs are added with add_stat, never through here."""
        pid = meta["id"]
        old = self.projects.get(pid)
        meta = copy.deepcopy(meta)
        meta["stats"] = old["stats"] if old else []
        new_requests = []
        if old is not None and meta.get("history", [])[:len(old["history"])] == old["history"]:
            start = len(old["history"])
            new_requests = [(start + i, text) for i, text in enumerate(meta.get("history", [])[start:])]
        else:                                      # new project, or the history was rewritten
            new_requests = list(enumerate(meta.get("history", [])))
        rewrite = old is not None and meta.get("history", [])[:len(old["history"])] != old["history"]
        self.projects[pid] = meta
        extra = {k: v for k, v in meta.items() if k not in COLUMNS + ("id", "owner", "created", "updated", "history", "stats")}
        row = (uuid.UUID(pid), uuid.UUID(meta["owner"]) if meta.get("owner") else None, meta["name"], meta.get("description", ""),
               _jsonb(meta.get("pages") or []), _jsonb(meta.get("brief") or {}), _jsonb(meta.get("brand") or {}),
               _jsonb(meta.get("assets") or []), list(meta.get("agents") or []), bool(meta.get("showcase")),
               _jsonb(meta.get("run") or {}), _jsonb(extra), meta.get("created") or time.time(), meta.get("updated") or time.time())
        now = time.time()

        async def job(con):
            async with con.transaction():
                await con.execute(
                    """INSERT INTO projects (id, owner_id, name, description, pages, brief, brand, assets, agents, showcase, run, extra, created, updated)
                       VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8::jsonb, $9, $10, $11::jsonb, $12::jsonb, $13, $14)
                       ON CONFLICT (id) DO UPDATE SET owner_id = $2, name = $3, description = $4, pages = $5::jsonb, brief = $6::jsonb,
                         brand = $7::jsonb, assets = $8::jsonb, agents = $9, showcase = $10, run = $11::jsonb, extra = $12::jsonb, updated = $14""",
                    *row)
                if rewrite:
                    await con.execute("DELETE FROM project_requests WHERE project_id = $1", row[0])
                for n, text in new_requests:
                    await con.execute("""INSERT INTO project_requests (project_id, n, text, ts) VALUES ($1, $2, $3, $4)
                                         ON CONFLICT (project_id, n) DO UPDATE SET text = $3""", row[0], n, text, now)
        self._write(job)

    def delete(self, pid: str):
        self.projects.pop(pid, None)
        self.versions.pop(pid, None)
        self.seq.pop(pid, None)
        key = uuid.UUID(pid)

        async def job(con):
            async with con.transaction():
                for table in ("projects", "project_requests", "agent_runs", "project_events", "versions", "files"):
                    await con.execute(f"DELETE FROM {table} WHERE {'id' if table == 'projects' else 'project_id'} = $1", key)
        self._write(job)
        shutil.rmtree(work_dir(pid), ignore_errors=True)

    # ------------------------------------------------------------ agent runs

    @staticmethod
    def _stat_from_row(r) -> dict:
        out = {k: r[k] for k in STAT_COLUMNS if r[k] is not None}
        out.update(json.loads(r["extra"]))
        return out

    def add_stat(self, pid: str, entry: dict):
        meta = self.projects.get(pid)
        if meta is None:
            return
        meta["stats"].append(copy.deepcopy(entry))
        del meta["stats"][:-MAX_STATS]
        values = [entry.get(k) for k in STAT_COLUMNS]
        values[STAT_COLUMNS.index("request")] = (entry.get("request") or "")[:500]
        values[STAT_COLUMNS.index("model")] = entry.get("model") or ""
        values[STAT_COLUMNS.index("seconds")] = entry.get("seconds") or 0
        extra = {k: v for k, v in entry.items() if k not in STAT_COLUMNS}

        async def job(con):
            await con.execute(f"""INSERT INTO agent_runs (project_id, {', '.join(STAT_COLUMNS)}, extra)
                                  VALUES ($1, {', '.join(f'${i + 2}' for i in range(len(STAT_COLUMNS)))}, ${len(STAT_COLUMNS) + 2}::jsonb)""",
                              uuid.UUID(pid), *values, _jsonb(extra))
        self._write(job)

    # ------------------------------------------------------------ the chat

    def next_seq(self, pid: str) -> int:
        self.seq[pid] = self.seq.get(pid, 0) + 1
        return self.seq[pid]

    def last_seq(self, pid: str) -> int:
        return self.seq.get(pid, 0)

    def add_event(self, pid: str, event: dict):
        data = copy.deepcopy(event)

        async def job(con):
            await con.execute("""INSERT INTO project_events (project_id, seq, type, ts, data) VALUES ($1, $2, $3, $4, $5::jsonb)
                                 ON CONFLICT (project_id, seq) DO NOTHING""",
                              uuid.UUID(pid), data["seq"], data["type"], data.get("ts") or time.time(), _jsonb(data))
        self._write(job)

    async def events(self, pid: str) -> list[dict]:
        await self.flush()
        rows = await self.pool.fetch("SELECT data FROM project_events WHERE project_id = $1 ORDER BY seq", uuid.UUID(pid))
        return [json.loads(r["data"]) for r in rows]

    # ------------------------------------------------------------ versions

    def version_list(self, pid: str) -> list[dict]:
        return copy.deepcopy(self.versions.get(pid, []))

    def add_version(self, pid: str, v: dict):
        self.versions.setdefault(pid, []).append(copy.deepcopy(v))

        async def job(con):
            await con.execute("""INSERT INTO versions (id, project_id, n, label, kind, agent, request, ts, files, size, hash)
                                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) ON CONFLICT (id) DO NOTHING""",
                              uuid.UUID(v["id"]), uuid.UUID(pid), v["n"], v["label"], v["kind"], v.get("agent"), v.get("request") or "",
                              v["ts"], v["files"], v["size"], v["hash"])
        self._write(job)

    def drop_version(self, pid: str, version_id: str):
        self.versions[pid] = [v for v in self.versions.get(pid, []) if v["id"] != version_id]

        async def job(con):
            await con.execute("DELETE FROM versions WHERE id = $1", uuid.UUID(version_id))
            await con.execute("DELETE FROM files WHERE area = 'version' AND version_id = $1", uuid.UUID(version_id))
        self._write(job)

    # ------------------------------------------------------------ the files table

    def index_files(self, pid: str | None, area: str, root: Path, version_id: str | None = None, agent_id: str | None = None,
                    skip: tuple = ()):
        """Record every file under `root` in `files` (each keeps its uuid while its path stays) and forget the ones gone.
        The folder is read when the job runs, in a thread, so callers never wait on hashing."""
        p_id = uuid.UUID(pid) if pid else None
        v_id = uuid.UUID(version_id) if version_id else None
        a_id = uuid.UUID(str(agent_id)) if agent_id else None

        def scan():
            if not root.exists():
                return []
            out = []
            for f in sorted(root.rglob("*")):
                rel = f.relative_to(root)
                if f.is_file() and not (rel.parts and rel.parts[0] in skip):
                    out.append((str(rel), f.stat().st_size, _hash_file(f)))
            return out

        async def job(con):
            rows = await asyncio.to_thread(scan)
            now = time.time()
            async with con.transaction():
                keep = [r[0] for r in rows]
                await con.execute("""DELETE FROM files WHERE project_id IS NOT DISTINCT FROM $1 AND agent_id IS NOT DISTINCT FROM $2
                                     AND area = $3 AND version_id IS NOT DISTINCT FROM $4 AND NOT (path = ANY($5::text[]))""",
                                  p_id, a_id, area, v_id, keep)
                for path, size, digest in rows:
                    await con.execute("""INSERT INTO files (project_id, agent_id, area, version_id, path, size, sha256, updated)
                                         VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                                         ON CONFLICT (project_id, agent_id, area, version_id, path)
                                         DO UPDATE SET size = $6, sha256 = $7, updated = CASE WHEN files.sha256 = $7 THEN files.updated ELSE $8 END""",
                                      p_id, a_id, area, v_id, path, size, digest, now)
        self._write(job)

    def index_site(self, pid: str):
        self.index_files(pid, "site", site_dir(pid))


store = Store()


# ---------------------------------------------------------------- agents' instructions (agents/<uuid>/instructions.md)

def instructions_path(agent_uuid) -> Path:
    return agent_dir(agent_uuid) / "instructions.md"


def read_instructions(agent_uuid, fallback: str = "") -> str:
    """An agent's instructions: its file, or `fallback` (the database column) while the file isn't written yet."""
    if fallback:
        return fallback
    p = instructions_path(agent_uuid)
    try:
        return p.read_text()
    except OSError:
        return ""


async def export_instructions(con) -> int:
    """Move instructions still in the database into their files (after migrations and after an agent is saved)."""
    rows = await con.fetch("SELECT pk, uuid, role FROM agents WHERE role <> ''")
    for r in rows:
        p = instructions_path(r["uuid"])
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(r["role"])
        tmp.replace(p)
        await con.execute("UPDATE agents SET role = '' WHERE pk = $1 AND role = $2", r["pk"], r["role"])
        digest = hashlib.sha256(r["role"].encode()).hexdigest()
        await con.execute("""INSERT INTO files (agent_id, area, path, size, sha256, updated) VALUES ($1, 'agent', 'instructions.md', $2, $3, $4)
                             ON CONFLICT (project_id, agent_id, area, version_id, path) DO UPDATE SET size = $2, sha256 = $3, updated = $4""",
                          r["uuid"], len(r["role"].encode()), digest, time.time())
    return len(rows)


async def forget_deleted_agents(con):
    """Remove the folders (and file rows) of agents that no longer exist."""
    alive = {str(r["uuid"]) for r in await con.fetch("SELECT uuid FROM agents")}
    if AGENTS.exists():
        for d in AGENTS.iterdir():
            if d.is_dir() and d.name not in alive:
                shutil.rmtree(d, ignore_errors=True)
    await con.execute("DELETE FROM files WHERE area = 'agent' AND NOT (agent_id::text = ANY($1::text[]))", list(alive))
