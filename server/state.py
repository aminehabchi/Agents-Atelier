"""Shared process state: the project store, versions (VERS), each project's live state (runs, live(), is_busy()),
a project's data (load_meta, save_meta, update_run, log_event), the events sent to open studios (broadcast, notify,
ws_send), and the settings the routes and the run pipeline both use."""

import asyncio
import contextlib
import json
import logging
import os
import time
import uuid
from pathlib import Path

from fastapi import HTTPException, WebSocket

from core import config
from core import db
from core import storage
from core.versions import Versions
from accounts import share
from crew.team_files import load_agents


ROOT = Path(__file__).resolve().parent.parent


store = storage.store          # every project's data, in Postgres (cached in memory); files are in STORAGE_DIR
site_dir = storage.site_dir    # works/<project>/site: the website the agents edit
work_dir = storage.work_dir    # works/<project>: site/, versions/, uploads/, checks/
VERS = Versions(store)
VERS.keep = lambda pid: runs.get(pid, {}).get("keep_versions")   # set from the owner's plan at each run
VERS.pinned = lambda pid: share.pinned.get(pid, set())             # versions frozen share links show: never pruned
logging.basicConfig(level=getattr(logging, config.LOG_LEVEL, logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agents-atelier")

MAX_BODY = config.MAX_BODY          # largest request body we accept (an image upload is ~6.7 MB as base64)
MAX_IMAGE = config.MAX_IMAGE        # largest reference image
MAX_ASSETS = config.MAX_ASSETS      # brand images (logo, photos) a project can start with
MAX_SITE_IMPORT = config.MAX_SITE_IMPORT   # largest existing site a project can start from (unpacked)
MAX_EDIT = config.MAX_EDIT          # largest file the Code tab will load or save
AGENT_TIMEOUT = config.AGENT_TIMEOUT   # seconds before a stuck agent is stopped
REVIEW_ROUNDS = config.REVIEW_ROUNDS   # times the Critic may send work back in one run
REVIEWER = "critic"                   # the agent whose reply can send work back (REDO lines)
ORCH_TIMEOUT = min(AGENT_TIMEOUT, 180) # the Orchestrator only reads and decides
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
TEXT_EXT = {".html", ".css", ".js", ".md", ".svg", ".json", ".txt", ".xml", ".webmanifest"}

CLAUDE = config.CLAUDE_BIN
STARTED = time.time()           # for the monitoring page's uptime
MODEL = config.AGENT_MODEL  # e.g. "sonnet" for faster runs; default = your Claude Code default


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


# Live state per project: running task, claude process, connected browser tabs.
runs: dict[str, dict] = {}


def live(pid: str) -> dict:
    return runs.setdefault(pid, {"task": None, "procs": set(), "clients": set()})


def kill_procs(st: dict):
    """Stop every `claude` process of a project (several agents may work at the same time)."""
    for proc in list(st["procs"]):
        if proc.returncode is None:
            proc.kill()
    st["procs"].clear()


def is_busy(pid: str) -> bool:
    t = runs.get(pid, {}).get("task")
    return bool(t and not t.done())


# ---------------------------------------------------------------- storage

PROJECT_GONE = "This project doesn't exist (it may have been deleted)."


def check_id(pid: str) -> str:
    try:
        return str(uuid.UUID(str(pid)))
    except ValueError:
        raise HTTPException(404, PROJECT_GONE)


def display_path(p: Path) -> str:
    """A short path for messages: relative to the app folder when possible."""
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def load_meta(pid: str) -> dict:
    """A project's data (a copy: change it, then save_meta)."""
    meta = store.get(check_id(pid))
    if meta is None:
        raise HTTPException(404, PROJECT_GONE)
    return meta


def save_meta(meta: dict, touch: bool = True):
    if touch:
        meta["updated"] = time.time()
    store.put(meta)


def log_event(pid: str, event: dict):
    """Number every event and save chat-relevant ones, so the chat can be restored later."""
    if event["type"] == "file_changed":
        event["seq"] = store.last_seq(pid)  # not saved; shares the last number
        return
    event["seq"] = store.next_seq(pid)
    event["ts"] = time.time()
    store.add_event(pid, event)


RESUMABLE = ("stopped", "error", "interrupted")


def mark_interrupted_runs():
    """A run that was 'running' when the server stopped can't still be running: mark it interrupted."""
    for m in store.all():
        if (m.get("run") or {}).get("status") == "running":
            m["run"]["status"] = "interrupted"
            save_meta(m, touch=False)


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
            "current": run.get("current") or nxt, "running": run.get("running") or [], "next": nxt, "error": run.get("error", ""),
            "resumable": run.get("status") in RESUMABLE and nxt is not None}


async def owner_of(meta: dict) -> dict | None:
    """The account whose agents a project uses (None for old projects with no owner: the shared agents)."""
    if not meta.get("owner"):
        return None
    row = await db.pool.fetchrow("SELECT id, role, email, language, plan, run_limit FROM users WHERE id = $1", uuid.UUID(meta["owner"]))
    return dict(row) if row else None


async def project_agents(meta: dict) -> list[dict]:
    return await load_agents(await owner_of(meta))


# ---------------------------------------------------------------- the marketplace (see market.py)

def _all_metas() -> list[dict]:
    return store.all()


def _has_preview(pid: str) -> bool:
    return (site_dir(pid) / "index.html").is_file()


# ---------------------------------------------------------------- stats

def record_stat(pid: str, entry: dict):
    store.add_stat(pid, entry)


# ---------------------------------------------------------------- agents

async def notify(pid: str, event: dict):
    """Tell the studios open on this project about something that isn't part of the chat (comments, people)."""
    event["project"] = pid
    for ws in list(live(pid)["clients"]):
        if not await ws_send(ws, event):
            live(pid)["clients"].discard(ws)


async def broadcast(pid: str, event: dict):
    event["project"] = pid
    log_event(pid, event)
    st = live(pid)
    for ws in list(st["clients"]):
        if not await ws_send(ws, event):         # a closed or stuck tab is dropped; it catches up when it reconnects
            st["clients"].discard(ws)


WS_SEND_TIMEOUT = 10              # seconds: a tab that stops reading is dropped instead of holding up a run


async def ws_send(ws: WebSocket, event: dict) -> bool:
    """Send one event to one tab, with a time limit. False when that tab is gone or stuck."""
    try:
        await asyncio.wait_for(ws.send_json(event), WS_SEND_TIMEOUT)
        return True
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:              # stuck: close it, so the studio reconnects and catches up on what it missed
        task = asyncio.create_task(_close_quietly(ws))
        _closing.add(task)
        task.add_done_callback(_closing.discard)
        return False
    except Exception:
        return False


_closing: set[asyncio.Task] = set()


async def _close_quietly(ws: WebSocket):
    with contextlib.suppress(Exception):
        await asyncio.wait_for(ws.close(code=1013), 5)
