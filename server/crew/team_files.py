"""The agents and teams, stored in Postgres (see db.py), with ownership.

Who sees what
  - Shared agents and teams (owner_id NULL) are seen by everyone; only admins create, change or delete them.
  - Every user can create their own agents and teams, or duplicate shared ones, and change those freely.
    Nobody else sees them.
  - A user's visible agents = the shared ones + their own. Ids are unique in that set: a user's agent can't take the
    id of a shared agent, and an admin can't give a new shared agent an id a user already uses.
  - Run order: the shared agents' order (set by admins), unless the user saved their own order (users.agent_order).
  - The built-in teams (Default, Quick, Polish, Full) are computed from each user's visible agents.

The agents/ folder still matters:
  _common.md        the shared rules every agent receives (a file; only admins change it in the app)
  _orchestrator.md  the Orchestrator's instructions
  NN-name.md        the source of the shared agents: `manage.py make-agents-migration` turns changes into a
                    migration in server/migrations/, applied on startup

Agent file format (also used for export and import):

  ---
  id: ux               # unique id (defaults to the file name without the number)
  name: UX             # label shown in the UI
  icon: compass        # an icon name from server/static/icons.js
  color: "#8b5cf6"
  default: true        # switched on for new projects
  description: Plans the page structure…   # one line shown in the UI
  model: sonnet        # optional: Claude model for this agent (empty = the server default)
  tools: web           # optional: also give it web search and page fetching (WebSearch, WebFetch); it may then
                       # only write research.md and plan.md (the server reverts anything else it changes)
  ---
  You are the UX agent...
"""

import json
import re
import unicodedata
import time
from pathlib import Path

import config
import db
import storage

AGENTS_DIR = config.AGENTS_DIR
COMMON_FILE = AGENTS_DIR / "_common.md"
PRESETS_FILE = AGENTS_DIR / "_presets.json"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ICON_RE = re.compile(r"^[a-z0-9-]{1,32}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9._\-\[\]]{0,64}$")
TOOL_SETS = {"", "web"}   # extra tool sets an agent can be given (see `tools:` above)
BUILTIN_TEAMS = ("default", "quick", "polish", "full")
TEAM_FORMAT = "agents-atelier-team"


def is_admin(user: dict | None) -> bool:
    return bool(user) and user.get("role") == "admin"


# Arabic letters in Latin, so a Darija page or team name still gives a readable file name ("حجز" -> "hjz").
ARABIC = dict(zip("ابتثجحخدذرزسشصضطظعغفقكلمنهوي", ["a", "b", "t", "th", "j", "h", "kh", "d", "dh", "r", "z", "s", "sh", "s", "d", "t",
                                                  "z", "a", "gh", "f", "q", "k", "l", "m", "n", "h", "w", "y"]))
ARABIC.update({"ء": "", "أ": "a", "إ": "i", "آ": "a", "ؤ": "w", "ئ": "y", "ى": "a", "ة": "a", "ڭ": "g", "ڤ": "v", "پ": "p", "چ": "ch"})
LIGATURES = {"ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "ð": "d", "þ": "th", "ł": "l", "đ": "d", "ı": "i"}


def ascii_text(text: str) -> str:
    """Letters with accents become plain letters (Équipe -> Equipe), Arabic letters are spelled in Latin."""
    text = "".join(LIGATURES.get(c, ARABIC.get(c, c)) for c in str(text).lower())
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def slugify(text: str, fallback: str = "item") -> str:
    return re.sub(r"[^a-z0-9]+", "-", ascii_text(text)).strip("-")[:32] or fallback


# ---------------------------------------------------------------- files: parsing, shared rules, first fill

def _parse_text(text: str) -> tuple[dict, str]:
    meta = {}
    if text.startswith("---") and text.count("---") >= 2:
        _, front, text = text.split("---", 2)
        for line in front.splitlines():
            line = line.split(" #", 1)[0].strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = (s.strip() for s in line.split(":", 1))
            value = value.strip('"').strip("'")
            meta[key] = {"true": True, "false": False}.get(value.lower(), value)
    return meta, text.strip()


def parse_agent_text(text: str, filename: str = "") -> dict:
    meta, role = _parse_text(text)
    stem = Path(filename).stem
    slug = stem.split("-", 1)[-1] if stem[:1].isdigit() and "-" in stem else stem
    return {"id": str(meta.get("id") or slug).strip().lower(), "name": str(meta.get("name") or slug.title()),
            "icon": str(meta.get("icon") or "bot"), "color": str(meta.get("color") or "#766a7c"),
            "default": meta.get("default", False) is True, "description": str(meta.get("description") or ""),
            "model": str(meta.get("model") or ""), "tools": str(meta.get("tools") or ""), "role": role}


def read_agent_files() -> list[dict]:
    """The agents in agents/NN-name.md, in file order (used to fill the shared agents)."""
    out, seen = [], set()
    for path in sorted(AGENTS_DIR.glob("*.md")):
        if path.name.startswith(("_", ".")):
            continue
        a = parse_agent_text(path.read_text(), path.name)
        a["default"] = _parse_text(path.read_text())[0].get("default", True) is True
        if a["id"] in seen or not a["role"] or not ID_RE.match(a["id"]):
            continue
        if a["tools"] not in TOOL_SETS:
            a["tools"] = ""
        seen.add(a["id"])
        out.append(a)
    return out


def read_preset_file() -> list[dict]:
    try:
        data = json.loads(PRESETS_FILE.read_text()) if PRESETS_FILE.exists() else []
    except ValueError:
        return []
    return [t for t in data if isinstance(t, dict)] if isinstance(data, list) else []


def load_common() -> str:
    return _parse_text(COMMON_FILE.read_text())[1] if COMMON_FILE.exists() else ""


def save_common(text: str):
    COMMON_FILE.write_text("---\n# Shared instructions given to EVERY agent (before its own role).\n---\n" + text.strip() + "\n")


def _render(agent: dict) -> str:
    desc = f"description: {agent['description']}\n" if agent.get("description") else ""
    desc += f"model: {agent['model']}\n" if agent.get("model") else ""
    desc += f"tools: {agent['tools']}\n" if agent.get("tools") else ""
    return (f"---\nid: {agent['id']}\nname: {agent['name']}\nicon: {agent['icon']}\n"
            f"color: \"{agent['color']}\"\ndefault: {str(agent['default']).lower()}\n{desc}---\n{agent['role'].strip()}\n")


# ---------------------------------------------------------------- reading

def _agent(row, user: dict | None) -> dict:
    shared = row["owner_id"] is None
    return {"id": row["slug"], "name": row["name"], "icon": row["icon"], "color": row["color"],
            "default": row["is_default"], "description": row["description"], "model": row["model"],
            "tools": row["tools"] if row["tools"] in TOOL_SETS else "", "role": storage.read_instructions(row["uuid"], row["role"]),
            "scope": "shared" if shared else "mine", "editable": is_admin(user) if shared else True,
            "updated": row["updated_at"].timestamp()}


async def load_agents(user: dict | None) -> list[dict]:
    """The agents `user` can see and use, in run order. user=None: the shared agents only."""
    uid = user["id"] if user else None
    rows = await db.pool.fetch(
        "SELECT * FROM agents WHERE owner_id IS NULL OR owner_id = $1 ORDER BY (owner_id IS NOT NULL), position, pk", uid)
    # the user's own agent wins if a later shared agent took the same id (e.g. after an agents migration)
    agents = list({r["slug"]: _agent(r, user) for r in rows}.values())
    order = list(await db.pool.fetchval("SELECT agent_order FROM users WHERE id = $1", uid) or []) if uid else []
    if order:
        rank = {a: i for i, a in enumerate(order)}
        base = {a["id"]: i for i, a in enumerate(agents)}
        agents.sort(key=lambda a: (rank[a["id"]], 0) if a["id"] in rank else (len(rank) + base[a["id"]], 1))
    for i, a in enumerate(agents, 1):
        a["file"] = f"{i:02d}-{a['id']}.md"
    return agents


async def default_ids(user: dict | None) -> list[str]:
    return [a["id"] for a in await load_agents(user) if a["default"]]


async def _find(user: dict | None, agent_id: str) -> dict:
    a = next((a for a in await load_agents(user) if a["id"] == agent_id), None)
    if not a:
        raise KeyError(agent_id)
    return a


async def _id_taken(user: dict | None, agent_id: str, shared: bool) -> bool:
    """A new agent's id must be free in the owner's visible set (and, for a shared agent, for every user)."""
    if shared:
        return bool(await db.pool.fetchval("SELECT 1 FROM agents WHERE slug = $1 LIMIT 1", agent_id))
    return bool(await db.pool.fetchval("SELECT 1 FROM agents WHERE slug = $1 AND (owner_id IS NULL OR owner_id = $2) LIMIT 1",
                                       agent_id, user["id"]))


# ---------------------------------------------------------------- editing

def validate(data: dict, *, new: bool) -> dict:
    """Return clean agent fields or raise ValueError with a readable message."""
    out = {}
    if new:
        agent_id = str(data.get("id", "")).strip().lower()
        if not ID_RE.match(agent_id):
            raise ValueError("Id must be 1–32 lowercase letters, numbers or dashes.")
        out["id"] = agent_id
    name = str(data.get("name", "")).strip()
    if not name or len(name) > 40 or "\n" in name:
        raise ValueError("Name is required (max 40 characters).")
    role = str(data.get("role", "")).strip()
    if len(role) < 10:
        raise ValueError("The role prompt is too short.")
    icon = str(data.get("icon") or "bot").strip()
    if not ICON_RE.match(icon):
        raise ValueError("Unknown icon.")
    color = str(data.get("color") or "#766a7c").strip()
    if not COLOR_RE.match(color):
        raise ValueError("Color must look like #a1b2c3.")
    description = " ".join(str(data.get("description") or "").split())
    if len(description) > 160:
        raise ValueError("Keep the description under 160 characters.")
    if " #" in description:
        raise ValueError("The description can't contain “ #”.")
    model = str(data.get("model") or "").strip()
    if not MODEL_RE.match(model):
        raise ValueError("Model must be an alias like sonnet or opus, or a full model id.")
    import models   # local import: models.py reads the CLI and agents/_models.json
    if not models.allowed(model):
        raise ValueError(f"“{model}” isn't in the model list. Pick one, or check it with the Claude CLI first.")
    tools = str(data.get("tools") or "").strip().lower()
    if tools not in TOOL_SETS:
        raise ValueError("Tools must be empty, or “web” for web search and page fetching.")
    import injection   # local import, like models above
    found = injection.suspicious(f"{name}\n{description}\n{role}", "a")
    if found:
        raise ValueError(f"These instructions can't be saved: they look like a prompt injection ({injection.describe(found)}). "
                         "Remove that part and try again.")
    role = injection.strip_invisible(role)
    out.update(name=name, role=role, icon=icon, color=color.lower(), default=bool(data.get("default", True)),
               description=description, model=model, tools=tools)
    return out


async def create_agent(user: dict, data: dict, position: int | None = None) -> dict:
    """Admins create shared agents unless they ask for scope "mine"; everyone else creates their own.
    `position` (0-based) places it in the run order."""
    agent = validate(data, new=True)
    if position is not None:
        try:
            position = int(position)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("The position must be a whole number (0 puts the agent first).")
    shared = is_admin(user) and data.get("scope") != "mine"
    if await _id_taken(user, agent["id"], shared):
        raise ValueError(f"An agent with id “{agent['id']}” already exists. Choose another id.")
    pos = await db.pool.fetchval("SELECT coalesce(max(position), -1) + 1 FROM agents WHERE owner_id IS NOT DISTINCT FROM $1",
                                 None if shared else user["id"])
    await db.pool.execute(
        """INSERT INTO agents (owner_id, slug, name, icon, color, is_default, description, model, tools, role, position)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)""",
        None if shared else user["id"], agent["id"], agent["name"], agent["icon"], agent["color"], agent["default"],
        agent["description"], agent["model"], agent["tools"], agent["role"], pos)
    if shared:
        await db.pool.execute("DELETE FROM shared_removed WHERE kind = 'agent' AND slug = $1", agent["id"])
    async with db.pool.acquire() as con:
        await storage.export_instructions(con)
    if position is not None:
        ids = [a["id"] for a in await load_agents(user) if a["id"] != agent["id"]]
        ids.insert(max(0, min(position, len(ids))), agent["id"])
        await reorder_agents(user, ids)
    return await _find(user, agent["id"])


async def update_agent(user: dict, agent_id: str, data: dict) -> dict:
    target = await _find(user, agent_id)
    if not target["editable"]:
        raise PermissionError("Only an admin can change a shared agent. Duplicate it to make your own version.")
    clean = validate({"tools": target.get("tools", ""), **data}, new=False)   # a form without `tools` keeps it
    await db.pool.execute(
        """UPDATE agents SET name = $3, icon = $4, color = $5, is_default = $6, description = $7, model = $8, tools = $9,
                             role = $10, origin = 'app', updated_at = now()
           WHERE slug = $1 AND owner_id IS NOT DISTINCT FROM $2""",
        agent_id, None if target["scope"] == "shared" else user["id"], clean["name"], clean["icon"], clean["color"],
        clean["default"], clean["description"], clean["model"], clean["tools"], clean["role"])
    async with db.pool.acquire() as con:
        await storage.export_instructions(con)
    return await _find(user, agent_id)


async def delete_agent(user: dict, agent_id: str):
    target = await _find(user, agent_id)
    if not target["editable"]:
        raise PermissionError("Only an admin can delete a shared agent.")
    if target["scope"] == "shared" and await db.pool.fetchval("SELECT count(*) FROM agents WHERE owner_id IS NULL") <= 1:
        raise ValueError("Keep at least one shared agent.")
    await db.pool.execute("DELETE FROM agents WHERE slug = $1 AND owner_id IS NOT DISTINCT FROM $2",
                          agent_id, None if target["scope"] == "shared" else user["id"])
    async with db.pool.acquire() as con:
        await storage.forget_deleted_agents(con)
    if target["scope"] == "shared":          # agent migrations won't bring it back
        await db.pool.execute("INSERT INTO shared_removed (kind, slug) VALUES ('agent', $1) ON CONFLICT DO NOTHING", agent_id)


async def reorder_agents(user: dict, ids: list[str]):
    """Save the user's run order. An admin's order also becomes the shared agents' order for everyone else."""
    agents = await load_agents(user)
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or sorted(ids) != sorted(a["id"] for a in agents):
        raise ValueError("The new order must list every agent exactly once.")
    await db.pool.execute("UPDATE users SET agent_order = $2 WHERE id = $1", user["id"], ids)
    if is_admin(user):
        shared = [i for i in ids if any(a["id"] == i and a["scope"] == "shared" for a in agents)]
        async with db.pool.acquire() as con:
            async with con.transaction():
                for pos, slug in enumerate(shared):
                    await con.execute("UPDATE agents SET position = $2 WHERE owner_id IS NULL AND slug = $1", slug, pos)


async def teams_with(user: dict, agent_id: str) -> list[dict]:
    return [{"id": p["id"], "name": p["name"], "builtin": p["builtin"], "scope": p["scope"]}
            for p in await load_presets(user) if agent_id in p["agents"]]


# ---------------------------------------------------------------- teams (presets)

def _builtin(agents: list[dict]) -> list[dict]:
    ids = [a["id"] for a in agents]
    pick = lambda wanted: [i for i in ids if i in wanted]
    base = {"builtin": True, "scope": "builtin", "editable": False, "prompt": ""}
    return [
        {"id": "default", "name": "Default", "agents": [a["id"] for a in agents if a["default"]], **base,
         "description": "The agents marked “on by default”: a good team for a new page."},
        {"id": "quick", "name": "Quick", "agents": pick({"html", "css"}), **base,
         "description": "Just markup and styles: fastest and cheapest, for small changes."},
        {"id": "polish", "name": "Polish", "agents": pick({"a11y", "perf", "critic"}), **base,
         "description": "Improve an existing page: accessibility, speed and a final review."},
        {"id": "full", "name": "Full", "agents": ids, **base,
         "description": "Every agent, in order: the most thorough result."},
    ]


async def load_presets(user: dict | None) -> list[dict]:
    agents = await load_agents(user)
    order = [a["id"] for a in agents]
    out = _builtin(agents)
    rows = await db.pool.fetch("SELECT * FROM teams WHERE owner_id IS NULL OR owner_id = $1 ORDER BY (owner_id IS NULL), created_at, pk",
                               user["id"] if user else None)
    for r in rows:
        shared = r["owner_id"] is None
        out.append({"id": r["slug"], "name": r["name"], "description": r["description"], "prompt": r["prompt"],
                    "agents": [i for i in order if i in r["agents"]],   # always in run order, missing agents dropped
                    "builtin": False, "scope": "shared" if shared else "mine",
                    "editable": is_admin(user) if shared else True,
                    "created": r["created_at"].timestamp(), "updated": r["updated_at"].timestamp()})
    return out


async def get_preset(user: dict | None, preset_id: str) -> dict:
    p = next((p for p in await load_presets(user) if p["id"] == preset_id), None)
    if not p:
        raise KeyError(preset_id)
    return p


async def _clean_team(user: dict, data: dict) -> dict:
    name = " ".join(str(data.get("name") or "").split())
    if not name or len(name) > 40:
        raise ValueError("Give the team a name (max 40 characters).")
    description = " ".join(str(data.get("description") or "").split())
    if len(description) > 200:
        raise ValueError("Keep the description under 200 characters.")
    prompt = "\n".join(line.rstrip() for line in str(data.get("prompt") or "").strip().splitlines())
    if len(prompt) > db.MAX_TEAM_PROMPT:
        raise ValueError(f"Keep the starting prompt under {db.MAX_TEAM_PROMPT} characters.")
    wanted = data.get("agents") or []
    if not isinstance(wanted, list):
        raise ValueError("List the team's agents by their ids, like [\"brief\", \"html\"].")
    ids = [a["id"] for a in await load_agents(user) if a["id"] in wanted]
    if not ids:
        raise ValueError("A team needs at least one agent.")
    return {"name": name, "description": description, "agents": ids, "prompt": prompt}


async def save_preset(user: dict, name: str, agent_ids: list[str], description: str = "", scope: str | None = None,
                      prompt: str = "") -> dict:
    """Create a team: shared when an admin makes it (unless scope is "mine"), the user's own otherwise.
    The id comes from the name."""
    clean = await _clean_team(user, {"name": name, "agents": agent_ids, "description": description, "prompt": prompt})
    shared = is_admin(user) and scope != "mine"
    taken = set(BUILTIN_TEAMS) | {p["id"] for p in await load_presets(user)}
    if shared:   # a shared team's id must also be free for every user
        taken |= {r["slug"] for r in await db.pool.fetch("SELECT slug FROM teams")}
    base = slugify(clean["name"], "team")
    pid, n = base, 2
    while pid in taken:
        pid, n = f"{base}-{n}", n + 1
    await db.pool.execute("INSERT INTO teams (owner_id, slug, name, description, agents, prompt) VALUES ($1, $2, $3, $4, $5, $6)",
                          None if shared else user["id"], pid, clean["name"], clean["description"], clean["agents"], clean["prompt"])
    if shared:
        await db.pool.execute("DELETE FROM shared_removed WHERE kind = 'team' AND slug = $1", pid)
    return await get_preset(user, pid)


async def update_preset(user: dict, preset_id: str, data: dict) -> dict:
    p = await get_preset(user, preset_id)
    if p["builtin"]:
        raise ValueError("Built-in teams can't be changed. Duplicate it to make your own version.")
    if not p["editable"]:
        raise PermissionError("Only an admin can change a shared team. Duplicate it to make your own version.")
    clean = await _clean_team(user, data)
    await db.pool.execute("""UPDATE teams SET name = $3, description = $4, agents = $5, prompt = $6, origin = 'app', updated_at = now()
                             WHERE slug = $1 AND owner_id IS NOT DISTINCT FROM $2""",
                          preset_id, None if p["scope"] == "shared" else user["id"],
                          clean["name"], clean["description"], clean["agents"], clean["prompt"])
    return await get_preset(user, preset_id)


async def delete_preset(user: dict, preset_id: str):
    p = await get_preset(user, preset_id)
    if p["builtin"]:
        raise ValueError("Built-in teams can't be deleted.")
    if not p["editable"]:
        raise PermissionError("Only an admin can delete a shared team.")
    await db.pool.execute("DELETE FROM teams WHERE slug = $1 AND owner_id IS NOT DISTINCT FROM $2",
                          preset_id, None if p["scope"] == "shared" else user["id"])
    if p["scope"] == "shared":
        await db.pool.execute("INSERT INTO shared_removed (kind, slug) VALUES ('team', $1) ON CONFLICT DO NOTHING", preset_id)


# ---------------------------------------------------------------- export & import
# Formats:
#   agent  → its Markdown file (frontmatter + role)
#   team   → JSON bundle {"format": "agents-atelier-team", "version": 1, "team": {...}, "agents": [agent, ...]}
#   all    → a .zip with agents/*.md, agents/_common.md, agents/_presets.json and manifest.json

AGENT_KEYS = ("id", "name", "icon", "color", "default", "description", "model", "tools", "role")


async def export_agent(user: dict, agent_id: str) -> tuple[str, str]:
    a = await _find(user, agent_id)
    return f"{a['id']}.md", _render(a)


async def export_team(user: dict, preset_id: str) -> tuple[str, dict]:
    t = await get_preset(user, preset_id)
    by_id = {a["id"]: a for a in await load_agents(user)}
    agents = [{k: by_id[i][k] for k in AGENT_KEYS} for i in t["agents"] if i in by_id]
    return f"team-{t['id']}.json", {"format": TEAM_FORMAT, "version": 1, "exported": time.time(),
                                    "team": {"name": t["name"], "description": t.get("description", ""), "agents": t["agents"],
                                             "prompt": t.get("prompt", "")},
                                    "agents": agents}


def _same_agent(a: dict, b: dict) -> bool:
    keys = ("name", "icon", "color", "default", "description", "model", "tools", "role")
    return all(str(a.get(k, "")).strip() == str(b.get(k, "")).strip() for k in keys)


async def plan_import(user: dict, items: list[dict], on_conflict: str = "rename") -> dict:
    """items: [{"kind": "agent"|"team"|"rules", ...}]. Returns the plan without changing anything.
    on_conflict for agents and teams that already exist (and differ): rename · replace · skip.
    A shared agent can only be replaced by an admin; for others it is imported as a renamed copy."""
    import models
    if on_conflict not in ("rename", "replace", "skip"):
        raise ValueError("on_conflict must be rename, replace or skip.")
    existing = {a["id"]: a for a in await load_agents(user)}
    taken = set(existing)
    if is_admin(user):   # new shared agents must not take an id a user already has
        taken |= {r["slug"] for r in await db.pool.fetch("SELECT slug FROM agents")}
    teams = await load_presets(user)
    plan, id_map = [], {}

    def free(aid: str) -> str:
        n, target = 2, f"{aid}-2"
        while target in taken:
            n, target = n + 1, f"{aid}-{n}"
        taken.add(target)
        return target

    for it in items:
        if it["kind"] != "agent":
            continue
        a, notes = dict(it["agent"]), []
        if a.get("model") and not models.allowed(a["model"]):
            notes.append(f"model “{a['model']}” isn't available here, so it will use the default model")
            a["model"] = ""
        try:
            validate({**a, "id": a.get("id", "")}, new=True)
        except ValueError as e:
            plan.append({"kind": "agent", "id": a.get("id"), "name": a.get("name"), "action": "error", "note": str(e)})
            continue
        aid = a["id"]
        mine = existing.get(aid)
        if mine and _same_agent(mine, a):
            action, target = "same", aid
            notes.append("identical to the agent you already have")
        elif mine and on_conflict == "replace" and mine["editable"]:
            action, target = "replace", aid
        elif mine and on_conflict == "skip":
            action, target = "skip", aid
            notes.append("an agent with this id already exists")
        elif aid in taken:
            action, target = "rename", free(aid)
            notes.append(f"an agent “{aid}” already exists" + ("" if not mine or mine["editable"] else " and is shared")
                         + f", imported as “{target}”")
        else:
            action, target = "add", aid
            taken.add(aid)
        id_map[aid] = target
        plan.append({"kind": "agent", "id": aid, "target": target, "name": a["name"], "action": action,
                     "note": "; ".join(notes), "data": {**a, "id": target}})

    for it in items:
        if it["kind"] == "team":
            t = it["team"] if isinstance(it["team"], dict) else {}
            t = {**t, "name": " ".join(str(t.get("name") or "").split())[:40]}
            raw_ids = t.get("agents") if isinstance(t.get("agents"), list) else []
            ids = [id_map.get(i, i) for i in raw_ids if isinstance(i, str)]
            known = [i for i in ids if i in existing or i in taken]
            missing = [i for i in ids if i not in known]
            same = next((p for p in teams if p["name"] == t.get("name") and sorted(p["agents"]) == sorted(known)), None)
            if not t.get("name") or not known:
                plan.append({"kind": "team", "name": t.get("name") or "(no name)", "action": "error",
                             "note": "a team needs a name and at least one agent that exists"})
                continue
            note = f"{len(missing)} unknown agent(s) left out: {', '.join(missing)}" if missing else ""
            if same:
                plan.append({"kind": "team", "name": t["name"], "action": "same", "note": "identical to a team you already have"})
            else:
                plan.append({"kind": "team", "name": t["name"], "action": "add", "note": note,
                             "data": {"name": t["name"], "description": t.get("description", ""), "agents": known,
                                      "prompt": t.get("prompt", "")}})
        elif it["kind"] == "rules":
            same = it["text"].strip() == load_common().strip()
            if same:
                plan.append({"kind": "rules", "name": "Shared rules", "action": "same", "note": ""})
            elif not is_admin(user):
                plan.append({"kind": "rules", "name": "Shared rules", "action": "skip", "note": "only an admin can change the shared rules"})
            else:
                plan.append({"kind": "rules", "name": "Shared rules", "action": "replace" if on_conflict == "replace" else "skip",
                             "note": "will replace the shared rules" if on_conflict == "replace"
                             else "kept the current rules. Choose “Replace” to import these rules",
                             "data": {"text": it["text"]}})
    return {"items": plan, "summary": {k: sum(1 for p in plan if p["action"] == k) for k in ("add", "rename", "replace", "skip", "same", "error")}}


async def apply_import(user: dict, plan: dict) -> dict:
    done = []
    for p in plan["items"]:
        if p["kind"] == "agent" and p["action"] in ("add", "rename"):
            await create_agent(user, p["data"]); done.append(p)
        elif p["kind"] == "agent" and p["action"] == "replace":
            await update_agent(user, p["target"], p["data"]); done.append(p)
    for p in plan["items"]:
        if p["kind"] == "team" and p["action"] == "add":
            d = p["data"]
            t = await save_preset(user, d["name"], d["agents"], d.get("description", ""), prompt=d.get("prompt", ""))
            p["target"] = t["id"]; done.append(p)
        elif p["kind"] == "rules" and p["action"] == "replace" and is_admin(user):
            save_common(p["data"]["text"]); done.append(p)
    return {"applied": len(done), "items": [{k: v for k, v in p.items() if k != "data"} for p in plan["items"]]}
