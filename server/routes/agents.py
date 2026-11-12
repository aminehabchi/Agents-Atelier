"""Agents and teams (presets): list, read, create, edit, order and delete; the shared rules; the model list and
check; export and import bundles; and the stats per agent (/api/stats)."""

import base64
import io
import json
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from accounts import auth
from accounts import keys
from accounts import plans
from crew import market
from crew import models as model_catalog
from crew import team_files
from crew.team_files import load_agents
from crew.team_files import load_common

from state import human_size, is_busy, load_meta, owner_of, runs, store
from web import can_access, current_user, page_of, require_admin
from project_data import ZIP_ERRORS, _unzip
from runs.safety import scrub_paths

router = APIRouter()


@router.get("/api/agents")
async def list_agents(request: Request, full: bool = False, project: str | None = None):
    """The agents you can see; with `project`, the agents that project runs on (those of its owner)."""
    who = current_user(request)
    if who and who["role"] == "unconfirmed":
        who = None
    if not who and (full or project):
        raise HTTPException(401, "Log in first.")
    if project:
        meta = load_meta(project)
        if not can_access(who, meta):
            raise HTTPException(404, "This project doesn't exist (it may have been deleted).")
        who = await owner_of(meta)
    keys = ("id", "name", "icon", "color", "default", "description", "model", "tools", "scope", "editable", "locked") + (("role", "file") if full else ())
    out = [{**{k: a[k] for k in keys}, "model_ok": model_catalog.allowed(a["model"])} for a in await load_agents(who)]
    if not full:
        return out
    see = sees_instructions(current_user(request))
    return [a if see and not a["locked"] else without_instructions(a) for a in out]


INSTRUCTIONS_LOCKED = ("Reading the agents' instructions comes with the Pro plan. On Starter you see what each agent does, "
                       "its results, its projects and its ratings, and every official agent works for you.")


def sees_instructions(user: dict | None) -> bool:
    """Starter members see what an agent does (name, description, stats, projects, ratings), not its instructions."""
    return bool(user) and user.get("role") != "unconfirmed" and plans.of(user).get("instructions", True)


def without_instructions(item: dict) -> dict:
    """No instructions: a Starter member's view, or a bought (locked) copy of a paid agent."""
    return {**item, "role": "", "instructions_locked": True}


async def _agent_call(call):
    try:
        return await call
    except KeyError:
        raise HTTPException(404, "Not found. It may have been deleted.")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/models")
async def list_models():
    return model_catalog.catalog()


@router.post("/api/models/check")
async def check_model(request: Request, body: dict):
    """One tiny call with this model, on the member's own Claude account (CLAUDE_KEYS=user)."""
    env = None
    if keys.USER_MODE:
        user = current_user(request)
        key = keys.decrypt(await auth.stored_claude_key(user["id"]))
        if not key:
            raise HTTPException(400, "Add your Claude API key in your profile first: checking a model makes a tiny call on your account.")
        env = keys.env_for(str(user["id"]), key)
    try:
        return await model_catalog.check(str(body.get("model") or ""), env=env)
    except ValueError as e:
        raise HTTPException(400, scrub_paths(keys.scrub(str(e), "")))


@router.get("/api/agents/common")
async def get_common(request: Request):
    plans.need(current_user(request), "instructions", INSTRUCTIONS_LOCKED)
    return {"text": load_common()}


@router.put("/api/agents/common")
async def put_common(request: Request, body: dict):
    require_admin(request)
    text = str(body.get("text", "")).strip()
    if len(text) < 10:
        raise HTTPException(400, "The shared rules are too short.")
    team_files.save_common(text)
    return {"text": load_common()}


@router.post("/api/agents/order")
async def order_agents(request: Request, body: dict):
    await _agent_call(team_files.reorder_agents(current_user(request), body.get("ids")))
    return await list_agents(request, full=True)


@router.post("/api/agents")
async def add_agent(request: Request, body: dict):
    plans.need(current_user(request), "custom_agents", "Making and editing your own agents and teams comes with the Pro plan. "
                                                     "The official agents and ready teams are yours to use.")
    return await _agent_call(team_files.create_agent(current_user(request), body, body.get("position")))


@router.get("/api/agents/{agent_id}")
async def get_agent(request: Request, agent_id: str):
    user = current_user(request)
    agents = await load_agents(user)
    i = next((i for i, a in enumerate(agents) if a["id"] == agent_id), None)
    if i is None:
        raise HTTPException(404, f"There's no agent called “{agent_id}”.")
    stats = next((b for b in (await get_stats(request))["agents"] if b["agent"] == agent_id), None)
    agent = agents[i] if sees_instructions(user) and not agents[i]["locked"] else without_instructions(agents[i])
    return {**agent, "model_ok": model_catalog.allowed(agents[i]["model"]), "position": i + 1, "total": len(agents),
            "prev": agents[i - 1]["id"] if i > 0 else None, "next": agents[i + 1]["id"] if i + 1 < len(agents) else None,
            "teams": await team_files.teams_with(user, agent_id), "stats": stats,
            "market": await market.listing_of(user, "agent", agent_id)}


@router.put("/api/agents/{agent_id}")
async def edit_agent(request: Request, agent_id: str, body: dict):
    plans.need(current_user(request), "custom_agents", "Making and editing your own agents and teams comes with the Pro plan. "
                                                     "The official agents and ready teams are yours to use.")
    agent = await _agent_call(team_files.update_agent(current_user(request), agent_id, body))
    await market.after_edit(current_user(request), "agent", agent_id)
    return agent


@router.delete("/api/agents/{agent_id}")
async def remove_agent(request: Request, agent_id: str):
    await _agent_call(team_files.delete_agent(current_user(request), agent_id))
    return {"ok": True}


# ---------------------------------------------------------------- presets

@router.get("/api/presets")
async def get_presets(request: Request):
    return await team_files.load_presets(current_user(request))


# ---------------------------------------------------------------- export & import of agents and teams

MAX_IMPORT_FILES = 300
MAX_IMPORT_BYTES = 5 * 1024 * 1024


def _download(name: str, content: bytes, media: str):
    return StreamingResponse(io.BytesIO(content), media_type=media,
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/agents/{agent_id}/export")
async def export_agent(request: Request, agent_id: str):
    plans.need(current_user(request), "instructions", "Exports contain the agents' instructions, which come with the Pro plan.")
    try:
        name, text = await team_files.export_agent(current_user(request), agent_id)
    except KeyError:
        raise HTTPException(404, f"There's no agent called “{agent_id}”.")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return _download(name, text.encode(), "text/markdown; charset=utf-8")


@router.get("/api/presets/{preset_id}/export")
async def export_team(request: Request, preset_id: str):
    plans.need(current_user(request), "instructions", "Exports contain the agents' instructions, which come with the Pro plan.")
    try:
        name, bundle = await team_files.export_team(current_user(request), preset_id)
    except KeyError:
        raise HTTPException(404, "This team doesn't exist (it may have been deleted).")
    return _download(name, json.dumps(bundle, indent=2).encode(), "application/json")


@router.get("/api/export")
async def export_everything(request: Request):
    """The agents you can see, the shared rules and your saved teams, as a .zip that /api/import understands."""
    plans.need(current_user(request), "instructions", "Exports contain the agents' instructions, which come with the Pro plan.")
    buf = io.BytesIO()
    user = current_user(request)
    agents = [a for a in await load_agents(user) if not a["locked"]]     # bought copies aren't theirs to share
    teams = await team_files.load_presets(user)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for a in agents:
            z.writestr(f"agents/{a['file']}", team_files._render(a))
        z.writestr("agents/_common.md", (team_files.COMMON_FILE.read_text() if team_files.COMMON_FILE.exists() else ""))
        z.writestr("agents/_presets.json", json.dumps([{k: p[k] for k in ("name", "description", "agents")}
                                                       for p in teams if not p["builtin"]], indent=2))
        z.writestr("manifest.json", json.dumps({"format": "agents-atelier-export", "version": 1, "exported": time.time(),
                                                "agents": len(agents)}, indent=2))
    return _download(f"agents-atelier-{time.strftime('%Y-%m-%d')}.zip", buf.getvalue(), "application/zip")


def _import_items(name: str, raw: bytes) -> list[dict]:
    """Turn an uploaded file into import items. Raises HTTPException with a readable message."""
    lower = name.lower()
    def text(b: bytes, label: str) -> str:
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(400, f"{label} isn't a UTF-8 text file.")

    def from_json(data, label):
        if isinstance(data, dict) and data.get("format") == team_files.TEAM_FORMAT:
            agents = [{"kind": "agent", "agent": {
                "id": str(a.get("id") or "").strip().lower(), "name": str(a.get("name") or ""), "icon": str(a.get("icon") or "bot"),
                "color": str(a.get("color") or "#766a7c"), "default": a.get("default") is True,
                "description": str(a.get("description") or ""), "model": str(a.get("model") or ""), "role": str(a.get("role") or "")}}
                for a in (data.get("agents") if isinstance(data.get("agents"), list) else []) if isinstance(a, dict)]
            return agents + [{"kind": "team", "team": data.get("team") or {}}]
        if isinstance(data, list):           # _presets.json: saved teams
            return [{"kind": "team", "team": t} for t in data if isinstance(t, dict)]
        raise HTTPException(400, f"{label} isn't a team file exported by Agents Atelier.")

    if lower.endswith(".md"):
        return [{"kind": "agent", "agent": team_files.parse_agent_text(text(raw, name), name)}]
    if lower.endswith(".json"):
        try:
            return from_json(json.loads(text(raw, name)), name)
        except ValueError:
            raise HTTPException(400, f"{name} isn't valid JSON.")
    if lower.endswith(".zip"):
        try:
            z = zipfile.ZipFile(io.BytesIO(raw))
        except ZIP_ERRORS:
            raise HTTPException(400, f"{name} isn't a valid .zip file.")
        infos = [i for i in z.infolist() if not i.is_dir() and not i.filename.startswith("__MACOSX")]
        if len(infos) > MAX_IMPORT_FILES or sum(i.file_size for i in infos) > MAX_IMPORT_BYTES:
            raise HTTPException(413, f"That .zip is too big to import (limit {MAX_IMPORT_FILES} files, {human_size(MAX_IMPORT_BYTES)} unpacked).")
        items = []
        for i in infos:
            base = Path(i.filename).name
            if base == "_common.md":
                items.append({"kind": "rules", "text": team_files._parse_text(text(_unzip(lambda: z.read(i), base), base))[1]})   # body only
            elif base.endswith(".md") and not base.startswith(("_", ".")) and base.lower() != "readme.md":
                items.append({"kind": "agent", "agent": team_files.parse_agent_text(text(_unzip(lambda: z.read(i), base), base), base)})
            elif base == "_presets.json" or (base.endswith(".json") and base != "manifest.json"):
                try:
                    items += from_json(json.loads(text(_unzip(lambda: z.read(i), base), base)), base)
                except (ValueError, HTTPException):
                    pass
        if not items:
            raise HTTPException(400, "No agents, teams or shared rules were found in that .zip.")
        return items
    raise HTTPException(400, "Import an agent (.md), a team (.json) or an export (.zip).")


@router.post("/api/import")
async def import_bundle(request: Request, body: dict):
    """Body: {name, data (base64), on_conflict: rename|replace|skip, apply: bool}.
    Without apply (or apply=false) it only returns the plan."""
    plans.need(current_user(request), "custom_agents", "Importing agents and teams comes with the Pro plan.")
    name = str(body.get("name") or "")
    try:
        raw = base64.b64decode(str(body.get("data") or ""), validate=True)
    except ValueError:
        raise HTTPException(400, "The file data is damaged. Try choosing the file again.")
    if not raw:
        raise HTTPException(400, "That file is empty.")
    items = [i for i in _import_items(name, raw) if i["kind"] != "skip"]
    try:
        plan = await team_files.plan_import(current_user(request), items, str(body.get("on_conflict") or "rename"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not body.get("apply"):
        return {"applied": 0, "items": [{k: v for k, v in p.items() if k != "data"} for p in plan["items"]], "summary": plan["summary"]}
    if any(p["kind"] == "agent" for p in plan["items"]) and any(is_busy(pid) for pid in list(runs)):
        raise HTTPException(409, "Agents are working in a project. Import when they finish.")
    return {**await _agent_call(team_files.apply_import(current_user(request), plan)), "summary": plan["summary"]}


@router.get("/api/presets/{preset_id}")
async def get_preset(request: Request, preset_id: str):
    try:
        team = await team_files.get_preset(current_user(request), preset_id)
    except KeyError:
        raise HTTPException(404, "This team doesn't exist (it may have been deleted).")
    return {**team, "market": None if team["builtin"] else await market.listing_of(current_user(request), "team", preset_id)}


@router.post("/api/presets")
async def add_preset(request: Request, body: dict):
    plans.need(current_user(request), "custom_agents", "Making and editing your own agents and teams comes with the Pro plan. "
                                                     "The official agents and ready teams are yours to use.")
    return await _agent_call(team_files.save_preset(current_user(request), body.get("name", ""), body.get("agents"),
                                                    str(body.get("description") or ""), body.get("scope"),
                                                    str(body.get("prompt") or "")))


@router.put("/api/presets/{preset_id}")
async def edit_preset(request: Request, preset_id: str, body: dict):
    plans.need(current_user(request), "custom_agents", "Making and editing your own agents and teams comes with the Pro plan. "
                                                     "The official agents and ready teams are yours to use.")
    team = await _agent_call(team_files.update_preset(current_user(request), preset_id, body))
    await market.after_edit(current_user(request), "team", preset_id)
    return team


@router.delete("/api/presets/{preset_id}")
async def remove_preset(request: Request, preset_id: str):
    await _agent_call(team_files.delete_preset(current_user(request), preset_id))
    return {"ok": True}


@router.get("/api/stats")
async def get_stats(request: Request, project: str | None = None, page: int | None = None, per_page: int = 10, mine: bool = False):
    user = current_user(request)
    if project:
        metas = [load_meta(project)]
        if not can_access(user, metas[0]):
            raise HTTPException(404, "This project doesn't exist (it may have been deleted).")
    else:
        metas = [m for m in store.all() if can_access(user, m) and (not mine or m.get("owner") == str(user["id"]))]
    entries = [dict(e, project=m["id"], project_name=m["name"]) for m in metas for e in m.get("stats", [])]
    entries.sort(key=lambda e: e["ts"])
    visible = await load_agents(user)
    names = {a["id"]: a for a in visible}
    names.setdefault("orchestrator", {"name": "Orchestrator", "icon": "cpu", "color": "#475569"})
    by_agent = {}
    for e in entries:
        b = by_agent.setdefault(e["agent"], {"agent": e["agent"], "name": names.get(e["agent"], {}).get("name", e["agent"]),
                                              "icon": names.get(e["agent"], {}).get("icon", "bot"),
                                              "color": names.get(e["agent"], {}).get("color", "#766a7c"),
                                              "runs": 0, "done": 0, "cost": 0.0, "seconds": 0.0, "tokens_in": 0, "tokens_out": 0, "models": set()})
        b["runs"] += 1
        b["done"] += e["status"] == "done"
        b["cost"] += e.get("cost") or 0
        b["seconds"] += e.get("seconds") or 0
        b["tokens_in"] += e.get("tokens_in") or 0
        b["tokens_out"] += e.get("tokens_out") or 0
        if e.get("model"):
            b["models"].add(e["model"])
    order = [a["id"] for a in visible]
    agents = sorted(by_agent.values(), key=lambda b: order.index(b["agent"]) if b["agent"] in order else 99)
    for b in agents:
        b["models"] = sorted(b["models"])
        b["avg_seconds"] = b["seconds"] / b["runs"] if b["runs"] else 0
        b["avg_cost"] = b["cost"] / b["runs"] if b["runs"] else 0
    totals = {"runs": len(entries), "cost": sum(e.get("cost") or 0 for e in entries),
              "seconds": sum(e.get("seconds") or 0 for e in entries),
              "tokens_in": sum(e.get("tokens_in") or 0 for e in entries),
              "tokens_out": sum(e.get("tokens_out") or 0 for e in entries),
              "failed": sum(e["status"] != "done" for e in entries)}
    if page is None:
        month_ago = time.time() - 30 * 86400        # the dashboard groups these into days in the viewer's time zone
        return {"totals": totals, "agents": agents, "recent": entries[-30:][::-1],
                "run_times": [e["ts"] for e in entries if e["ts"] >= month_ago]}
    recent = page_of(entries[::-1], page, per_page)   # every run, newest first, one page at a time
    return {"totals": totals, "agents": agents, "recent": recent.pop("items"), "recent_pager": recent}
