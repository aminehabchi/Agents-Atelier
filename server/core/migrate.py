"""Database migrations: numbered files in server/migrations/, applied once each, in order, when the server starts.

  NNNN_name.sql    SQL run as one transaction
  NNNN_name.json   shared agents and teams: {"agents": [...], "teams": [...]} (see apply_data)

Applied versions are recorded in schema_migrations, with a checksum: a migration that changed after it was applied is
reported, not re-run. Never edit an applied migration; add a new one. `manage.py make-agents-migration` writes the next
agents migration from agents/*.md and agents/_presets.json.
"""

import hashlib
import json
import logging
import re
from pathlib import Path

log = logging.getLogger("agents-atelier.migrate")

DIR = Path(__file__).resolve().parent.parent / "migrations"   # server/migrations/
NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.(sql|json)$")
LOCK = 4242          # one server at a time migrates

TRACKING = """CREATE TABLE IF NOT EXISTS schema_migrations (
    version    integer PRIMARY KEY,
    name       text NOT NULL,
    checksum   text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)"""


def available() -> list[dict]:
    out = []
    for path in sorted(DIR.glob("*")):
        m = NAME.match(path.name)
        if m:
            text = path.read_text()
            out.append({"version": int(m.group(1)), "name": path.name, "kind": m.group(3), "path": path, "text": text,
                        "checksum": hashlib.sha256(text.encode()).hexdigest()})
    versions = [m["version"] for m in out]
    if len(versions) != len(set(versions)):
        raise SystemExit(f"Two migrations share a number in {DIR}. Renumber one of them.")
    return out


async def applied(con) -> dict[int, dict]:
    await con.execute(TRACKING)
    return {r["version"]: dict(r) for r in await con.fetch("SELECT * FROM schema_migrations")}


async def apply_all(con) -> list[str]:
    """Apply every pending migration. Returns the names applied."""
    done = []
    async with con.transaction():
        await con.execute("SELECT pg_advisory_xact_lock($1)", LOCK)
        seen = await applied(con)
        for m in available():
            if m["version"] in seen:
                if seen[m["version"]]["checksum"] != m["checksum"]:
                    log.warning("Migration %s changed after it was applied. It isn't run again; add a new migration instead.", m["name"])
                continue
            async with con.transaction():
                if m["kind"] == "sql":
                    await con.execute(m["text"])
                else:
                    await apply_data(con, json.loads(m["text"]))
                await con.execute("INSERT INTO schema_migrations (version, name, checksum) VALUES ($1, $2, $3)",
                                  m["version"], m["name"], m["checksum"])
            log.info("Applied migration %s", m["name"])
            done.append(m["name"])
    return done


async def apply_data(con, data: dict):
    """Shared agents and teams. Each one is added if it isn't there, refreshed if a migration wrote it last, and left
    alone if an admin edited it in the app (origin 'app') or deleted it (shared_removed)."""
    removed = {(r["kind"], r["slug"]) for r in await con.fetch("SELECT kind, slug FROM shared_removed")}
    existing = {r["slug"]: r for r in await con.fetch("SELECT slug, origin, position FROM agents WHERE owner_id IS NULL")}
    order = [a["slug"] for a in data.get("agents", [])]
    for a in data.get("agents", []):
        if ("agent", a["slug"]) in removed:
            continue
        values = (a["slug"], a["name"], a.get("icon", "bot"), a.get("color", "#766a7c"), bool(a.get("default", False)),
                  a.get("description", ""), a.get("model", ""), a.get("tools", ""), a["role"])
        row = existing.get(a["slug"])
        if row is None:
            await con.execute(
                """INSERT INTO agents (owner_id, slug, name, icon, color, is_default, description, model, tools, role, position, origin)
                   VALUES (NULL, $1, $2, $3, $4, $5, $6, $7, $8, $9, 100000, 'migration')""", *values)
        elif row["origin"] == "migration":
            await con.execute(
                """UPDATE agents SET name = $2, icon = $3, color = $4, is_default = $5, description = $6, model = $7,
                   tools = $8, role = $9, updated_at = now() WHERE owner_id IS NULL AND slug = $1""", *values)
    await _place_new_agents(con, order, set(existing))

    for t in data.get("teams", []):
        if ("team", t["slug"]) in removed:
            continue
        values = (t["slug"], t["name"], t.get("description", ""), list(t.get("agents", [])), t.get("prompt", ""))
        row = await con.fetchrow("SELECT origin FROM teams WHERE owner_id IS NULL AND slug = $1", t["slug"])
        if row is None:
            await con.execute("""INSERT INTO teams (owner_id, slug, name, description, agents, prompt, origin)
                                 VALUES (NULL, $1, $2, $3, $4, $5, 'migration')""", *values)
        elif row["origin"] == "migration":
            await con.execute("""UPDATE teams SET name = $2, description = $3, agents = $4, prompt = $5, updated_at = now()
                                 WHERE owner_id IS NULL AND slug = $1""", *values)


async def _place_new_agents(con, order: list[str], had: set[str]):
    """Keep the shared order an admin may have set; put each new agent right after its predecessor in `order`."""
    current = [r["slug"] for r in await con.fetch("SELECT slug FROM agents WHERE owner_id IS NULL ORDER BY position, pk")
               if r["slug"] in had]
    for i, slug in enumerate(order):
        if slug in had or slug in current:
            continue
        before = next((order[j] for j in range(i - 1, -1, -1) if order[j] in current), None)
        current.insert(current.index(before) + 1 if before else 0, slug)
    for pos, slug in enumerate(current):
        await con.execute("UPDATE agents SET position = $2 WHERE owner_id IS NULL AND slug = $1", slug, pos)


def _agents_module():
    """server/crew/team_files.py (agents and teams), with server/ on the path so it imports from anywhere
    (manage.py and the tests run from the project root)."""
    import sys
    here = str(Path(__file__).resolve().parent.parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    from crew import team_files
    return team_files


def snapshot_from_files() -> dict:
    """The shared agents and teams as agents/*.md and agents/_presets.json describe them now."""
    store = _agents_module()
    out = {"agents": [], "teams": []}
    for a in store.read_agent_files():
        out["agents"].append({"slug": a["id"], "name": a["name"], "icon": a["icon"], "color": a["color"], "default": a["default"],
                              "description": a["description"], "model": a["model"], "tools": a["tools"], "role": a["role"]})
    for t in store.read_preset_file():
        slug = store.slugify(t.get("id") or t.get("name") or "team")
        out["teams"].append({"slug": slug, "name": str(t.get("name") or slug)[:40], "description": str(t.get("description") or "")[:200],
                             "agents": [str(x) for x in t.get("agents") or []], "prompt": str(t.get("prompt") or "")})
    return out


def latest_data() -> dict:
    """What the data migrations add up to: the last version of each agent and team, in the last known order."""
    agents, teams, order = {}, {}, []
    for m in available():
        if m["kind"] != "json":
            continue
        data = json.loads(m["text"])
        for a in data.get("agents", []):
            agents[a["slug"]] = a
        for t in data.get("teams", []):
            teams[t["slug"]] = t
        if data.get("agents"):
            order = [a["slug"] for a in data["agents"]]
    return {"agents": [agents[s] for s in order if s in agents] + [a for s, a in agents.items() if s not in order],
            "teams": list(teams.values())}


def write_agents_migration(name: str = "shared_agents") -> Path | None:
    """The next NNNN_<name>.json with every agent and team that differs from the migrations so far (None if none)."""
    now, before = snapshot_from_files(), latest_data()
    old_agents = {a["slug"]: a for a in before["agents"]}
    old_teams = {t["slug"]: t for t in before["teams"]}
    changed_agents = [a for a in now["agents"] if old_agents.get(a["slug"]) != a]
    changed_teams = [t for t in now["teams"] if old_teams.get(t["slug"]) != t]
    order_changed = [a["slug"] for a in now["agents"]] != [a["slug"] for a in before["agents"]]
    if not (changed_agents or changed_teams or order_changed):
        return None
    number = max([m["version"] for m in available()] or [0]) + 1
    path = DIR / f"{number:04d}_{re.sub(r'[^a-z0-9_]+', '_', name.lower()).strip('_') or 'shared_agents'}.json"
    # Every agent is listed so the order is known; unchanged ones are rewritten with the same content.
    path.write_text(json.dumps({"agents": now["agents"], "teams": changed_teams}, indent=2, ensure_ascii=False) + "\n")
    return path
