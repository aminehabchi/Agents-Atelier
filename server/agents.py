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
import re
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"
COMMON_FILE = AGENTS_DIR / "_common.md"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ICON_RE = re.compile(r"^[a-z0-9-]{1,32}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9._\-\[\]]{0,64}$")
PRESETS_FILE = AGENTS_DIR / "_presets.json"


def _parse(path: Path) -> tuple[dict, str]:
    text = path.read_text()
    meta = {}
    if text.startswith("---"):
        _, front, text = text.split("---", 2)
        for line in front.splitlines():
            line = line.split(" #", 1)[0].strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = (s.strip() for s in line.split(":", 1))
            value = value.strip('"').strip("'")
            meta[key] = {"true": True, "false": False}.get(value.lower(), value)
    return meta, text.strip()


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


# ---------------------------------------------------------------- team presets

def builtin_presets() -> list[dict]:
    ids = [a["id"] for a in load_agents()]
    pick = lambda wanted: [i for i in ids if i in wanted]
    return [
        {"id": "default", "name": "Default", "agents": default_ids(), "builtin": True,
         "description": "The agents marked “on by default”."},
        {"id": "quick", "name": "Quick", "agents": pick({"html", "css"}), "builtin": True,
         "description": "Just markup and styles — fastest and cheapest."},
        {"id": "polish", "name": "Polish", "agents": pick({"a11y", "perf", "critic"}), "builtin": True,
         "description": "Improve an existing page: accessibility, speed, final review."},
        {"id": "full", "name": "Full", "agents": ids, "builtin": True,
         "description": "Every agent, in order."},
    ]


def load_presets() -> list[dict]:
    ids = {a["id"] for a in load_agents()}
    custom = json.loads(PRESETS_FILE.read_text()) if PRESETS_FILE.exists() else []
    for p in custom:
        p["agents"] = [i for i in p["agents"] if i in ids]
        p["builtin"] = False
    return builtin_presets() + custom


def save_preset(name: str, agent_ids: list[str]) -> dict:
    name = " ".join(str(name).split())[:40]
    if not name:
        raise ValueError("Give the preset a name.")
    ids = [a["id"] for a in load_agents() if a["id"] in agent_ids]
    if not ids:
        raise ValueError("A preset needs at least one agent.")
    custom = json.loads(PRESETS_FILE.read_text()) if PRESETS_FILE.exists() else []
    pid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "preset"
    if pid in {p["id"] for p in builtin_presets()}:
        pid += "-custom"
    custom = [p for p in custom if p["id"] != pid] + [{"id": pid, "name": name, "agents": ids}]
    PRESETS_FILE.write_text(json.dumps(custom, indent=2))
    return {"id": pid, "name": name, "agents": ids, "builtin": False}


def delete_preset(preset_id: str):
    custom = json.loads(PRESETS_FILE.read_text()) if PRESETS_FILE.exists() else []
    if not any(p["id"] == preset_id for p in custom):
        raise KeyError(preset_id)
    PRESETS_FILE.write_text(json.dumps([p for p in custom if p["id"] != preset_id], indent=2))
