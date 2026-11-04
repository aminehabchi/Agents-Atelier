"""Loads and edits the agent team in the agents/ folder (re-read on every call, so edits apply live).

agents/
  _common.md        instructions shared by every agent
  01-brief.md       one file per agent; the number in the file name sets the run order
  02-ux.md
  ...

Each agent file is a small frontmatter block followed by the agent's role prompt:

  ---
  id: ux               # unique id (defaults to the file name without the number)
  name: UX             # label shown in the UI
  icon: compass        # an icon name from server/static/icons.js
  color: "#8b5cf6"
  default: true        # switched on for new projects
  description: Plans the page structure…   # one line shown in the UI
  model: sonnet        # optional: Claude model for this agent (empty = the server default)
  ---
  You are the UX agent...

Files starting with "_" or "." are not agents.
"""

import json
import os
import re
import time
from pathlib import Path

import config

AGENTS_DIR = config.AGENTS_DIR
COMMON_FILE = AGENTS_DIR / "_common.md"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ICON_RE = re.compile(r"^[a-z0-9-]{1,32}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9._\-\[\]]{0,64}$")
PRESETS_FILE = AGENTS_DIR / "_presets.json"


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


def _parse(path: Path) -> tuple[dict, str]:
    return _parse_text(path.read_text())


def load_common() -> str:
    return _parse(COMMON_FILE)[1] if COMMON_FILE.exists() else ""


def save_common(text: str):
    COMMON_FILE.write_text("---\n# Shared instructions given to EVERY agent (before its own role).\n---\n" + text.strip() + "\n")


def load_agents() -> list[dict]:
    agents, seen = [], set()
    for path in sorted(AGENTS_DIR.glob("*.md")):
        if path.name.startswith(("_", ".")):
            continue
        meta, role = _parse(path)
        slug = path.stem.split("-", 1)[-1] if path.stem[:1].isdigit() else path.stem
        agent_id = str(meta.get("id") or slug)
        if agent_id in seen or not role:
            continue
        seen.add(agent_id)
        agents.append({
            "id": agent_id,
            "name": str(meta.get("name") or agent_id.title()),
            "icon": str(meta.get("icon") or "bot"),
            "color": str(meta.get("color") or "#766a7c"),
            "default": meta.get("default", True) is True,
            "description": str(meta.get("description") or ""),
            "model": str(meta.get("model") or ""),
            "role": role,
            "file": path.name,
        })
    return agents


def default_ids() -> list[str]:
    return [a["id"] for a in load_agents() if a["default"]]


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
    out.update(name=name, role=role, icon=icon, color=color.lower(), default=bool(data.get("default", True)),
               description=description, model=model)
    return out


def _render(agent: dict) -> str:
    desc = f"description: {agent['description']}\n" if agent.get("description") else ""
    desc += f"model: {agent['model']}\n" if agent.get("model") else ""
    return (f"---\nid: {agent['id']}\nname: {agent['name']}\nicon: {agent['icon']}\n"
            f"color: \"{agent['color']}\"\ndefault: {str(agent['default']).lower()}\n{desc}---\n{agent['role'].strip()}\n")


def _write_all(agents: list[dict]):
    """Rewrite every agent file with fresh order numbers (01-, 02-, ...)."""
    keep = set()
    for i, a in enumerate(agents, 1):
        name = f"{i:02d}-{a['id']}.md"
        keep.add(name)
        (AGENTS_DIR / name).write_text(_render(a))
    for path in AGENTS_DIR.glob("*.md"):
        if not path.name.startswith(("_", ".")) and path.name not in keep:
            path.unlink()


def update_agent(agent_id: str, data: dict) -> dict:
    agents = load_agents()
    target = next((a for a in agents if a["id"] == agent_id), None)
    if not target:
        raise KeyError(agent_id)
    target.update(validate(data, new=False))
    _write_all(agents)
    return target


def create_agent(data: dict, position: int | None = None) -> dict:
    agents = load_agents()
    agent = validate(data, new=True)
    if any(a["id"] == agent["id"] for a in agents):
        raise ValueError(f"An agent with id “{agent['id']}” already exists.")
    agents.insert(len(agents) if position is None else max(0, min(position, len(agents))), agent)
    _write_all(agents)
    return agent


def delete_agent(agent_id: str):
    agents = load_agents()
    if not any(a["id"] == agent_id for a in agents):
        raise KeyError(agent_id)
    if len(agents) == 1:
        raise ValueError("Keep at least one agent.")
    _write_all([a for a in agents if a["id"] != agent_id])


def reorder_agents(ids: list[str]):
    agents = {a["id"]: a for a in load_agents()}
    if sorted(ids) != sorted(agents):
        raise ValueError("The new order must list every agent exactly once.")
    _write_all([agents[i] for i in ids])


# ---------------------------------------------------------------- teams (presets)
# Built-in teams are computed from the current agents; saved teams live in agents/_presets.json.

def builtin_presets() -> list[dict]:
    ids = [a["id"] for a in load_agents()]
    pick = lambda wanted: [i for i in ids if i in wanted]
    return [
        {"id": "default", "name": "Default", "agents": default_ids(), "builtin": True,
         "description": "The agents marked “on by default” — a good team for a new page."},
        {"id": "quick", "name": "Quick", "agents": pick({"html", "css"}), "builtin": True,
         "description": "Just markup and styles — fastest and cheapest, for small changes."},
        {"id": "polish", "name": "Polish", "agents": pick({"a11y", "perf", "critic"}), "builtin": True,
         "description": "Improve an existing page: accessibility, speed and a final review."},
        {"id": "full", "name": "Full", "agents": ids, "builtin": True,
         "description": "Every agent, in order — the most thorough result."},
    ]


def _read_custom() -> list[dict]:
    try:
        return json.loads(PRESETS_FILE.read_text()) if PRESETS_FILE.exists() else []
    except ValueError:
        return []


def _write_custom(items: list[dict]):
    tmp = PRESETS_FILE.with_name(PRESETS_FILE.name + ".tmp")
    tmp.write_text(json.dumps(items, indent=2))
    tmp.replace(PRESETS_FILE)


def load_presets() -> list[dict]:
    order = [a["id"] for a in load_agents()]
    out = builtin_presets()
    for p in _read_custom():
        out.append({"id": p["id"], "name": p["name"], "description": p.get("description", ""),
                    "agents": [i for i in order if i in p.get("agents", [])],   # always in run order
                    "builtin": False, "created": p.get("created"), "updated": p.get("updated")})
    return out


def get_preset(preset_id: str) -> dict:
    p = next((p for p in load_presets() if p["id"] == preset_id), None)
    if not p:
        raise KeyError(preset_id)
    return p


def _clean_team(data: dict) -> dict:
    name = " ".join(str(data.get("name") or "").split())
    if not name or len(name) > 40:
        raise ValueError("Give the team a name (max 40 characters).")
    description = " ".join(str(data.get("description") or "").split())
    if len(description) > 200:
        raise ValueError("Keep the description under 200 characters.")
    wanted = list(data.get("agents") or [])
    ids = [a["id"] for a in load_agents() if a["id"] in wanted]
    if not ids:
        raise ValueError("A team needs at least one agent.")
    return {"name": name, "description": description, "agents": ids}


def save_preset(name: str, agent_ids: list[str], description: str = "") -> dict:
    """Create a saved team. The id comes from the name and is made unique."""
    clean = _clean_team({"name": name, "agents": agent_ids, "description": description})
    custom = _read_custom()
    taken = {p["id"] for p in custom} | {p["id"] for p in builtin_presets()}
    base = re.sub(r"[^a-z0-9]+", "-", clean["name"].lower()).strip("-")[:32] or "team"
    pid, n = base, 2
    while pid in taken:
        pid, n = f"{base}-{n}", n + 1
    now = time.time()
    custom.append({"id": pid, **clean, "created": now, "updated": now})
    _write_custom(custom)
    return get_preset(pid)


def update_preset(preset_id: str, data: dict) -> dict:
    if preset_id in {p["id"] for p in builtin_presets()}:
        raise ValueError("Built-in teams can't be changed. Duplicate it to make your own version.")
    custom = _read_custom()
    p = next((p for p in custom if p["id"] == preset_id), None)
    if not p:
        raise KeyError(preset_id)
    p.update(_clean_team(data), updated=time.time())
    _write_custom(custom)
    return get_preset(preset_id)


def delete_preset(preset_id: str):
    if preset_id in {p["id"] for p in builtin_presets()}:
        raise ValueError("Built-in teams can't be deleted.")
    custom = _read_custom()
    if not any(p["id"] == preset_id for p in custom):
        raise KeyError(preset_id)
    _write_custom([p for p in custom if p["id"] != preset_id])


def teams_with(agent_id: str) -> list[dict]:
    return [{"id": p["id"], "name": p["name"], "builtin": p["builtin"]} for p in load_presets() if agent_id in p["agents"]]


# ---------------------------------------------------------------- export & import
# Formats:
#   agent  → its Markdown file (frontmatter + role), as stored in agents/
#   team   → JSON bundle {"format": "agents-atelier-team", "version": 1, "team": {...}, "agents": [agent, ...]}
#   all    → a .zip with agents/*.md, agents/_common.md, agents/_presets.json and manifest.json

TEAM_FORMAT = "agents-atelier-team"


def export_agent(agent_id: str) -> tuple[str, str]:
    a = next((a for a in load_agents() if a["id"] == agent_id), None)
    if not a:
        raise KeyError(agent_id)
    return f"{a['id']}.md", _render(a)


def export_team(preset_id: str) -> tuple[str, dict]:
    t = get_preset(preset_id)
    by_id = {a["id"]: a for a in load_agents()}
    agents = [{k: by_id[i][k] for k in ("id", "name", "icon", "color", "default", "description", "model", "role")}
              for i in t["agents"] if i in by_id]
    return f"team-{t['id']}.json", {"format": TEAM_FORMAT, "version": 1, "exported": time.time(),
                                    "team": {"name": t["name"], "description": t.get("description", ""), "agents": t["agents"]},
                                    "agents": agents}


def parse_agent_text(text: str, filename: str = "") -> dict:
    meta, role = _parse_text(text)
    stem = Path(filename).stem
    slug = stem.split("-", 1)[-1] if stem[:1].isdigit() and "-" in stem else stem
    return {"id": str(meta.get("id") or slug).strip().lower(), "name": str(meta.get("name") or slug.title()),
            "icon": str(meta.get("icon") or "bot"), "color": str(meta.get("color") or "#766a7c"),
            "default": meta.get("default", False) is True, "description": str(meta.get("description") or ""),
            "model": str(meta.get("model") or ""), "role": role}


def _same_agent(a: dict, b: dict) -> bool:
    keys = ("name", "icon", "color", "default", "description", "model", "role")
    return all(str(a.get(k, "")).strip() == str(b.get(k, "")).strip() for k in keys)


def plan_import(items: list[dict], on_conflict: str = "rename") -> dict:
    """items: [{"kind": "agent"|"team"|"rules", ...}]. Returns the plan without changing anything.
    on_conflict for agents and teams that already exist (and differ): rename · replace · skip."""
    import models
    if on_conflict not in ("rename", "replace", "skip"):
        raise ValueError("on_conflict must be rename, replace or skip.")
    existing = {a["id"]: a for a in load_agents()}
    taken = set(existing)
    teams = load_presets()
    plan, id_map = [], {}

    for it in items:
        if it["kind"] != "agent":
            continue
        a, notes = dict(it["agent"]), []
        if a.get("model") and not models.allowed(a["model"]):
            notes.append(f"model “{a['model']}” isn't available here — it will use the default model")
            a["model"] = ""
        try:
            validate({**a, "id": a.get("id", "")}, new=True)
        except ValueError as e:
            plan.append({"kind": "agent", "id": a.get("id"), "name": a.get("name"), "action": "error", "note": str(e)})
            continue
        aid = a["id"]
        if aid in existing and _same_agent(existing[aid], a):
            action, target = "same", aid
            notes.append("identical to the agent you already have")
        elif aid in existing:
            if on_conflict == "replace":
                action, target = "replace", aid
            elif on_conflict == "skip":
                action, target = "skip", aid
                notes.append("an agent with this id already exists")
            else:
                n, target = 2, f"{aid}-2"
                while target in taken:
                    n, target = n + 1, f"{aid}-{n}"
                taken.add(target)
                action = "rename"
                notes.append(f"an agent “{aid}” already exists — imported as “{target}”")
        else:
            action, target = "add", aid
            taken.add(aid)
        id_map[aid] = target
        plan.append({"kind": "agent", "id": aid, "target": target, "name": a["name"], "action": action,
                     "note": "; ".join(notes), "data": {**a, "id": target}})

    for it in items:
        if it["kind"] == "team":
            t = it["team"]
            ids = [id_map.get(i, i) for i in t.get("agents", [])]
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
                             "data": {"name": t["name"], "description": t.get("description", ""), "agents": known}})
        elif it["kind"] == "rules":
            same = it["text"].strip() == load_common().strip()
            plan.append({"kind": "rules", "name": "Shared rules", "action": "same" if same else ("replace" if on_conflict == "replace" else "skip"),
                         "note": "" if same else ("will replace your shared rules" if on_conflict == "replace"
                                                  else "kept yours — choose “Replace” to import these rules"),
                         "data": {"text": it["text"]}})
    return {"items": plan, "summary": {k: sum(1 for p in plan if p["action"] == k) for k in ("add", "rename", "replace", "skip", "same", "error")}}


def apply_import(plan: dict) -> dict:
    done = []
    for p in plan["items"]:
        if p["kind"] == "agent" and p["action"] in ("add", "rename"):
            create_agent(p["data"]); done.append(p)
        elif p["kind"] == "agent" and p["action"] == "replace":
            update_agent(p["target"], p["data"]); done.append(p)
    for p in plan["items"]:
        if p["kind"] == "team" and p["action"] == "add":
            d = p["data"]
            t = save_preset(d["name"], d["agents"], d.get("description", ""))
            p["target"] = t["id"]; done.append(p)
        elif p["kind"] == "rules" and p["action"] == "replace":
            save_common(p["data"]["text"]); done.append(p)
    return {"applied": len(done), "items": [{k: v for k, v in p.items() if k != "data"} for p in plan["items"]]}
