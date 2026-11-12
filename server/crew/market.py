"""The marketplace: agents and teams presented as freelancers people hire.

  Official      the shared agents and teams (owner_id NULL). Everyone already has them.
  Community     a user's own agent or team they published: it waits for an admin (listing 'pending'), then shows in
                the catalogue ('listed'). Editing a listed one sends it back for review.
  Hiring        copies a community agent (or a team with its agents) into your own agents, marked hired_from, so it
                runs in your projects like any agent of yours. Hires are counted on the original.
  Profiles      runs, jobs, projects, average time and cost, the Critic's average score and the ratings of an agent
                and of the copies hired from it. The portfolio shows your own projects it worked on and other
                people's projects only when their owner chose to show them (project "showcase").
  Ratings       a project's owner gives each agent that worked on it a thumbs up or down, with an optional comment.
"""

import re
import uuid

from crew import team_files as store
from core import db
from core import storage

AGENT_CATEGORIES = ("planning", "writing", "design", "code", "growth", "quality")
TEAM_CATEGORIES = ("build", "improve", "add")
SHARED_AGENT_CATEGORY = {
    "brief": "planning", "research": "planning", "auditor": "planning", "ux": "planning",
    "copy": "writing", "content": "writing", "translator": "writing", "legal": "writing",
    "design": "design", "3d-design": "design", "depth": "design", "3d-scene": "code", "assets": "design", "dark-theme": "design", "anim": "design", "gallery": "design",
    "html": "code", "css": "code", "js": "code", "responsive": "code", "forms": "code", "tidy": "code",
    "seo": "growth", "conversion": "growth", "local": "growth", "pricing": "growth",
    "whatsapp": "growth", "email": "growth", "call": "growth", "telegram": "growth", "qr": "growth",
    "a11y": "quality", "perf": "quality", "qa": "quality", "critic": "quality",
}
SHARED_TEAM_CATEGORY = {
    "improve-imported": "improve", "redesign": "improve", "boost-conversions": "improve", "seo-speed": "improve",
    "accessibility-audit": "improve", "code-cleanup": "improve",
    "multilingual": "add", "legal-pages": "add", "dark-mode": "add", "forms": "add", "add-3d": "add", "contact-options": "add",
}
REQUIRED = "brief"                 # always runs (see the studio)
CORE = {"brief", "html", "css"}    # never trimmed from a team to fit a budget
SCORE_RE = re.compile(r"(\d{1,2}(?:[.,]\d)?)\s*/\s*10\b")
# when an agent has never run anywhere: seconds and API-equivalent cost, by model family
DEFAULT_SECONDS = 40
DEFAULT_COST = {"opus": 0.45, "sonnet": 0.15, "haiku": 0.04, "fable": 0.6}


def critic_score(text: str) -> float | None:
    """The score out of 10 in the Critic's final reply ("Score: 8.5/10"), if it gave one."""
    m = SCORE_RE.search(text or "")
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return value if 0 <= value <= 10 else None


def _uid(user: dict | None):
    return user["id"] if user else None


def _is_admin(user: dict | None) -> bool:
    return store.is_admin(user)


# ---------------------------------------------------------------- who ran what

async def _agent_rows() -> list:
    return await db.pool.fetch("""SELECT a.pk, a.uuid, a.owner_id, a.slug, a.name, a.icon, a.color, a.description, a.model, a.tools,
                                         a.role, a.is_default, a.listing, a.category, a.listing_note, a.listed_at, a.hired_from,
                                         a.created_at, a.updated_at, u.name AS owner_name
                                  FROM agents a LEFT JOIN users u ON u.id = a.owner_id""")


def _resolver(rows):
    """(project owner, agent id in a stat) -> the agent row's pk: the owner's own agent wins over a shared one."""
    own = {(str(r["owner_id"]), r["slug"]): r["pk"] for r in rows if r["owner_id"] is not None}
    shared = {r["slug"]: r["pk"] for r in rows if r["owner_id"] is None}
    return lambda owner, slug: own.get((str(owner), slug)) if owner and (str(owner), slug) in own else shared.get(slug)


def _family(rows, pk) -> set:
    """An agent and the copies hired from it."""
    return {pk} | {r["pk"] for r in rows if r["hired_from"] == pk}


def run_stats(metas: list[dict], rows) -> dict:
    """pk -> runs, finished jobs, seconds, cost, the projects it worked on and the Critic scores of its runs."""
    resolve = _resolver(rows)
    out = {}
    for m in metas:
        entries = m.get("stats") or []
        # the Critic's score belongs to every agent of the same run (runs are told apart by their start time,
        # or by their request for stats recorded before runs had one)
        run_key = lambda e: e.get("run") or e.get("request")
        scores = {run_key(e): e["score"] for e in entries if e.get("score") is not None}
        for e in entries:
            if e.get("agent") == "orchestrator":
                continue
            pk = resolve(m.get("owner"), e.get("agent"))
            if pk is None:
                continue
            s = out.setdefault(pk, {"runs": 0, "done": 0, "seconds": 0.0, "cost": 0.0, "projects": set(), "scored": {}})
            s["runs"] += 1
            s["done"] += e.get("status") == "done"
            s["seconds"] += e.get("seconds") or 0
            s["cost"] += e.get("cost") or 0
            s["projects"].add(m["id"])
            if run_key(e) in scores:
                s["scored"][(m["id"], run_key(e))] = scores[run_key(e)]
    return out


def _sum_stats(stats: dict, pks: set) -> dict:
    runs = sum(stats[p]["runs"] for p in pks if p in stats)
    done = sum(stats[p]["done"] for p in pks if p in stats)
    seconds = sum(stats[p]["seconds"] for p in pks if p in stats)
    cost = sum(stats[p]["cost"] for p in pks if p in stats)
    projects = set().union(*(stats[p]["projects"] for p in pks if p in stats)) if pks else set()
    scores = [v for p in pks if p in stats for v in stats[p]["scored"].values()]
    return {"runs": runs, "jobs": done, "projects": len(projects), "avg_seconds": seconds / runs if runs else None,
            "avg_cost": cost / runs if runs else None, "score": round(sum(scores) / len(scores), 1) if scores else None,
            "scored_runs": len(scores), "_projects": projects}


async def _ratings(pks: set | None = None) -> list:
    rows = await db.pool.fetch("""SELECT r.agent_pk, r.value, r.comment, r.updated_at, r.project, u.name, u.email
                                  FROM agent_ratings r JOIN users u ON u.id = r.user_id ORDER BY r.updated_at DESC""")
    return [r for r in rows if pks is None or r["agent_pk"] in pks]


def _rating_summary(ratings) -> dict:
    up = sum(r["value"] > 0 for r in ratings)
    down = len(ratings) - up
    return {"up": up, "down": down, "count": len(ratings), "percent": round(100 * up / len(ratings)) if ratings else None}


def _first_name(name: str, email: str = "") -> str:
    return (name or "").split(" ")[0] or (email or "").split("@")[0] or "A member"


# ---------------------------------------------------------------- the catalogue

def _agent_category(r) -> str:
    return r["category"] if r["category"] in AGENT_CATEGORIES else SHARED_AGENT_CATEGORY.get(r["slug"], "code" if r["owner_id"] is None else "")


def _team_category(r) -> str:
    return r["category"] if r["category"] in TEAM_CATEGORIES else SHARED_TEAM_CATEGORY.get(r["slug"], "build")


def _visible_in_market(r, user) -> bool:
    return r["owner_id"] is None or r["listing"] == "listed" or (user and r["owner_id"] == user["id"]) or _is_admin(user)


def _agent_item(r, user, stats, rows, ratings) -> dict:
    fam = _family(rows, r["pk"])
    st = _sum_stats(stats, fam)
    st.pop("_projects")
    official = r["owner_id"] is None
    return {"pk": r["pk"], "id": r["slug"], "name": r["name"], "icon": r["icon"], "color": r["color"],
            "description": r["description"], "model": r["model"], "web": r["tools"] == "web", "default": r["is_default"],
            "category": _agent_category(r), "official": official, "listing": "listed" if official else r["listing"],
            "by": "Agents Atelier" if official else _first_name(r["owner_name"]),
            "mine": bool(user) and r["owner_id"] == user["id"], "copy": r["hired_from"] is not None,
            "hires": sum(1 for x in rows if x["hired_from"] == r["pk"]),
            "rating": _rating_summary([x for x in ratings if x["agent_pk"] in fam]),
            "listed_at": (r["listed_at"] or r["created_at"]).timestamp(), **st,
            **dict(zip(("est_seconds", "est_cost"), agent_estimate(r, stats, rows)[:2]))}


async def catalogue(user: dict | None, metas: list[dict]) -> dict:
    """Every agent and team in the catalogue: the official ones and the listed community ones."""
    rows = await _agent_rows()
    stats = run_stats(metas, rows)
    ratings = await _ratings()
    agents = [_agent_item(r, user, stats, rows, ratings) for r in rows
              if (r["owner_id"] is None or r["listing"] == "listed") and r["hired_from"] is None]
    teams = [await _team_item(t, user, stats, rows, ratings) for t in await _team_rows()
             if (t["owner_id"] is None or t["listing"] == "listed") and t["hired_from"] is None]
    my_copies = {r["hired_from"]: r["slug"] for r in rows if user and r["owner_id"] == user["id"] and r["hired_from"]}
    for a in agents:
        a["hired_as"] = my_copies.get(a["pk"])
    return {"agents": agents, "teams": teams, "agent_categories": AGENT_CATEGORIES, "team_categories": TEAM_CATEGORIES}


async def _team_rows() -> list:
    return await db.pool.fetch("""SELECT t.pk, t.owner_id, t.slug, t.name, t.description, t.agents, t.prompt, t.listing,
                                         t.category, t.listing_note, t.listed_at, t.hired_from, t.created_at, u.name AS owner_name
                                  FROM teams t LEFT JOIN users u ON u.id = t.owner_id""")


def _team_members(t, rows) -> list:
    """The agent rows a team runs, as its owner sees them (their own agent wins over a shared one)."""
    resolve = _resolver(rows)
    by_pk = {r["pk"]: r for r in rows}
    out = []
    for slug in t["agents"]:
        pk = resolve(t["owner_id"], slug)
        if pk in by_pk:
            out.append(by_pk[pk])
    return out


async def _team_item(t, user, stats, rows, ratings) -> dict:
    members = _team_members(t, rows)
    est = estimate_rows(members, stats, rows)
    fam_pks = set().union(*(_family(rows, m["pk"]) for m in members)) if members else set()
    official = t["owner_id"] is None
    hires = await db.pool.fetchval("SELECT count(*) FROM teams WHERE hired_from = $1", t["pk"])
    return {"pk": t["pk"], "id": t["slug"], "name": t["name"], "description": t["description"], "prompt": t["prompt"],
            "category": _team_category(t), "official": official, "listing": "listed" if official else t["listing"],
            "by": "Agents Atelier" if official else _first_name(t["owner_name"]),
            "mine": bool(user) and t["owner_id"] == user["id"],
            "agents": [{"pk": m["pk"], "id": m["slug"], "name": m["name"], "icon": m["icon"], "color": m["color"]} for m in members],
            "hires": hires, "rating": _rating_summary([x for x in ratings if x["agent_pk"] in fam_pks]),
            "seconds": est["seconds"], "cost": est["cost"], "listed_at": (t["listed_at"] or t["created_at"]).timestamp()}


# ---------------------------------------------------------------- estimates

def _default_cost(model: str) -> float:
    model = (model or "").lower()
    return next((c for k, c in DEFAULT_COST.items() if k in model), 0.2)


def agent_estimate(r, stats, rows) -> tuple[float, float, bool]:
    """(seconds, cost, measured) for one run of this agent: its own history and its copies', else a default."""
    st = _sum_stats(stats, _family(rows, r["pk"]))
    if st["runs"]:
        return st["avg_seconds"], st["avg_cost"] or _default_cost(r["model"]), True
    return DEFAULT_SECONDS, _default_cost(r["model"]), False


def estimate_rows(members, stats, rows, pages: int = 1) -> dict:
    per = []
    scale = 1 + 0.25 * max(0, pages - 1)          # more pages: more markup, copy and styles to write
    for m in members:
        s, c, measured = agent_estimate(m, stats, rows)
        per.append({"id": m["slug"], "name": m["name"], "icon": m["icon"], "color": m["color"], "default": m["is_default"],
                    "seconds": round(s * scale, 1), "cost": round(c * scale, 4), "measured": measured})
    return {"agents": per, "seconds": round(sum(a["seconds"] for a in per)), "cost": round(sum(a["cost"] for a in per), 2)}


# ---------------------------------------------------------------- profiles

async def agent_profile(user: dict, pk: int, metas: list[dict], has_preview) -> dict:
    rows = await _agent_rows()
    r = next((x for x in rows if x["pk"] == pk), None)
    if r is None or not _visible_in_market(r, user):
        raise KeyError(pk)
    stats = run_stats(metas, rows)
    fam = _family(rows, pk)
    ratings = await _ratings(fam)
    item = _agent_item(r, user, stats, rows, ratings)
    projects = _sum_stats(stats, fam)["_projects"]
    by_id = {m["id"]: m for m in metas}
    portfolio = []
    for pid in projects:
        m = by_id.get(pid)
        if not m:
            continue
        mine = m.get("owner") == str(user["id"])
        if mine or m.get("showcase"):
            portfolio.append({"id": pid, "name": m["name"], "description": m.get("description", ""), "mine": mine,
                              "preview": has_preview(pid), "updated": m.get("updated", 0)})
    portfolio.sort(key=lambda p: p["updated"], reverse=True)
    reviews = [{"value": x["value"], "comment": x["comment"], "by": _first_name(x["name"], x["email"]),
                "when": x["updated_at"].timestamp()} for x in ratings if x["comment"]][:200]
    teams = [{"pk": t["pk"], "id": t["slug"], "name": t["name"]} for t in await _team_rows()
             if (t["owner_id"] is None or t["listing"] == "listed") and t["hired_from"] is None
             and r["slug"] in t["agents"] and (t["owner_id"] is None or t["owner_id"] == r["owner_id"])]
    copy_of = next((x for x in rows if x["pk"] == r["hired_from"]), None) if r["hired_from"] else None
    mine_copy = next((x["slug"] for x in rows if x["hired_from"] == pk and x["owner_id"] == user["id"]), None)
    return {**item, "role": storage.read_instructions(r["uuid"], r["role"]), "teams": teams, "portfolio": portfolio[:200], "reviews": reviews,
            "hired_as": mine_copy, "copy_of": {"pk": copy_of["pk"], "name": copy_of["name"]} if copy_of else None,
            "note": r["listing_note"] if item["mine"] else ""}


async def team_profile(user: dict, pk: int, metas: list[dict]) -> dict:
    rows = await _agent_rows()
    t = next((x for x in await _team_rows() if x["pk"] == pk), None)
    if t is None or not _visible_in_market(t, user):
        raise KeyError(pk)
    stats = run_stats(metas, rows)
    ratings = await _ratings()
    item = await _team_item(t, user, stats, rows, ratings)
    members = _team_members(t, rows)
    est = estimate_rows(members, stats, rows)
    item["agents"] = [{**a, "pk": m["pk"], "description": m["description"], "official": m["owner_id"] is None,
                       "rating": _rating_summary([x for x in ratings if x["agent_pk"] in _family(rows, m["pk"])])}
                      for a, m in zip(est["agents"], members)]
    hired = await db.pool.fetchval("SELECT slug FROM teams WHERE hired_from = $1 AND owner_id = $2", pk, user["id"])
    return {**item, "hired_as": hired, "note": t["listing_note"] if item["mine"] else ""}


# ---------------------------------------------------------------- publishing

async def _own_row(user: dict, kind: str, slug: str):
    table = "agents" if kind == "agent" else "teams"
    r = await db.pool.fetchrow(f"SELECT * FROM {table} WHERE owner_id = $1 AND slug = $2", user["id"], slug)
    if r is None:
        raise KeyError(slug)
    return r


async def listing_of(user: dict, kind: str, slug: str) -> dict | None:
    """The marketplace state of one of your own agents or teams (None for a shared one)."""
    table = "agents" if kind == "agent" else "teams"
    r = await db.pool.fetchrow(f"SELECT pk, listing, category, listing_note, hired_from FROM {table} WHERE owner_id = $1 AND slug = $2",
                               user["id"], slug)
    if r is None:
        shared = await db.pool.fetchval(f"SELECT pk FROM {table} WHERE owner_id IS NULL AND slug = $1", slug)
        return {"pk": shared, "listing": "official"} if shared else None
    return {"pk": r["pk"], "listing": r["listing"], "category": r["category"], "note": r["listing_note"],
            "copy": r["hired_from"] is not None}


async def publish(user: dict, kind: str, slug: str, category: str) -> dict:
    cats = AGENT_CATEGORIES if kind == "agent" else TEAM_CATEGORIES
    if category not in cats:
        raise ValueError("Pick a category for the marketplace.")
    r = await _own_row(user, kind, slug)
    if r["hired_from"] is not None:
        raise ValueError("You hired this one from the marketplace, so you can't publish it as yours. Make your own agent instead.")
    table = "agents" if kind == "agent" else "teams"
    state = "listed" if _is_admin(user) else "pending"       # an admin's own listing needs no review
    await db.pool.execute(f"""UPDATE {table} SET listing = $3, category = $4, listing_note = '',
                                 listed_at = CASE WHEN $3 = 'listed' THEN now() ELSE listed_at END
                              WHERE owner_id = $1 AND slug = $2""", user["id"], slug, state, category)
    return await listing_of(user, kind, slug)


async def withdraw(user: dict, kind: str, slug: str) -> dict:
    await _own_row(user, kind, slug)
    table = "agents" if kind == "agent" else "teams"
    await db.pool.execute(f"UPDATE {table} SET listing = 'private', listing_note = '' WHERE owner_id = $1 AND slug = $2", user["id"], slug)
    return await listing_of(user, kind, slug)


async def after_edit(user: dict, kind: str, slug: str):
    """A listed agent or team that its (non-admin) owner changed goes back to the admins for review."""
    if _is_admin(user):
        return
    table = "agents" if kind == "agent" else "teams"
    await db.pool.execute(f"UPDATE {table} SET listing = 'pending' WHERE owner_id = $1 AND slug = $2 AND listing = 'listed'",
                          user["id"], slug)


async def pending() -> dict:
    agents = await db.pool.fetch("""SELECT a.pk, a.uuid, a.slug, a.name, a.icon, a.color, a.description, a.role, a.model, a.tools, a.category,
                                           a.updated_at, u.name AS owner_name, u.email
                                    FROM agents a JOIN users u ON u.id = a.owner_id WHERE a.listing = 'pending' ORDER BY a.updated_at""")
    teams = await db.pool.fetch("""SELECT t.pk, t.slug, t.name, t.description, t.prompt, t.agents, t.category, t.updated_at,
                                          u.name AS owner_name, u.email
                                   FROM teams t JOIN users u ON u.id = t.owner_id WHERE t.listing = 'pending' ORDER BY t.updated_at""")
    pack = lambda r: {**{k: (v.timestamp() if hasattr(v, "timestamp") else list(v) if isinstance(v, list) else v)
                         for k, v in dict(r).items() if k not in ("owner_name", "email")},
                      "by": f"{r['owner_name'] or 'A member'} ({r['email']})"}
    agents = [{**dict(r), "role": storage.read_instructions(r["uuid"], r["role"])} for r in agents]
    return {"agents": [{k: v for k, v in pack(r).items() if k != "uuid"} for r in agents], "teams": [pack(r) for r in teams]}


async def review(kind: str, pk: int, decision: str, note: str = "") -> dict:
    if decision not in ("approve", "reject"):
        raise ValueError("Approve or reject it.")
    note = " ".join(str(note or "").split())[:300]
    table = "agents" if kind == "agent" else "teams"
    row = await db.pool.fetchrow(f"SELECT pk, listing FROM {table} WHERE pk = $1 AND owner_id IS NOT NULL", pk)
    if row is None:
        raise KeyError(pk)
    if decision == "approve":
        await db.pool.execute(f"UPDATE {table} SET listing = 'listed', listed_at = coalesce(listed_at, now()), listing_note = '' WHERE pk = $1", pk)
    else:
        await db.pool.execute(f"UPDATE {table} SET listing = 'rejected', listing_note = $2 WHERE pk = $1", pk, note)
    return {"ok": True, "listing": "listed" if decision == "approve" else "rejected"}


# ---------------------------------------------------------------- hiring

async def _free_slug(user: dict, base: str) -> str:
    taken = {r["slug"] for r in await db.pool.fetch("SELECT slug FROM agents WHERE owner_id IS NULL OR owner_id = $1", user["id"])}
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base[:28]}-{n}", n + 1
    return slug


async def _copy_agent(user: dict, r) -> str:
    """Copy agent row `r` into the user's own agents (or return the copy they already have)."""
    have = await db.pool.fetchval("SELECT slug FROM agents WHERE owner_id = $1 AND hired_from = $2", user["id"], r["pk"])
    if have:
        return have
    slug = await _free_slug(user, r["slug"])
    pos = await db.pool.fetchval("SELECT coalesce(max(position), -1) + 1 FROM agents WHERE owner_id = $1", user["id"])
    await db.pool.execute(
        """INSERT INTO agents (owner_id, slug, name, icon, color, is_default, description, model, tools, role, position, hired_from)
           VALUES ($1, $2, $3, $4, $5, false, $6, $7, $8, $9, $10, $11)""",
        user["id"], slug, r["name"], r["icon"], r["color"], r["description"], r["model"], r["tools"],
        storage.read_instructions(r["uuid"], r["role"]), pos, r["pk"])
    async with db.pool.acquire() as con:
        await storage.export_instructions(con)
    return slug


async def hire_agent(user: dict, pk: int) -> dict:
    r = await db.pool.fetchrow("SELECT * FROM agents WHERE pk = $1", pk)
    if r is None or not (r["owner_id"] is None or r["listing"] == "listed"):
        raise KeyError(pk)
    if r["owner_id"] is None:
        return {"id": r["slug"], "already": True}           # official agents are everyone's already
    if r["owner_id"] == user["id"]:
        return {"id": r["slug"], "already": True}
    had = await db.pool.fetchval("SELECT slug FROM agents WHERE owner_id = $1 AND hired_from = $2", user["id"], pk)
    slug = await _copy_agent(user, r)
    return {"id": slug, "already": bool(had)}


async def hire_team(user: dict, pk: int) -> dict:
    t = await db.pool.fetchrow("SELECT * FROM teams WHERE pk = $1", pk)
    if t is None or not (t["owner_id"] is None or t["listing"] == "listed"):
        raise KeyError(pk)
    if t["owner_id"] is None or t["owner_id"] == user["id"]:
        return {"id": t["slug"], "already": True}
    had = await db.pool.fetchval("SELECT slug FROM teams WHERE owner_id = $1 AND hired_from = $2", user["id"], pk)
    if had:
        return {"id": had, "already": True}
    rows = await _agent_rows()
    ids = []
    for m in _team_members(t, rows):
        ids.append(m["slug"] if m["owner_id"] is None else await _copy_agent(user, await db.pool.fetchrow("SELECT * FROM agents WHERE pk = $1", m["pk"])))
    team = await store.save_preset(user, t["name"], ids, t["description"], scope="mine", prompt=t["prompt"])
    await db.pool.execute("UPDATE teams SET hired_from = $3 WHERE owner_id = $1 AND slug = $2", user["id"], team["id"], pk)
    return {"id": team["id"], "already": False, "agents": ids}


# ---------------------------------------------------------------- ratings

async def agent_pk_for(owner_id, slug: str) -> int | None:
    """The agent row a project owner's agent id means (their own agent wins over a shared one)."""
    return await db.pool.fetchval("""SELECT pk FROM agents WHERE slug = $1 AND (owner_id = $2 OR owner_id IS NULL)
                                     ORDER BY owner_id IS NULL LIMIT 1""", slug, uuid.UUID(str(owner_id)) if owner_id else None)


async def project_ratings(user: dict, meta: dict) -> dict:
    rows = await db.pool.fetch("""SELECT a.slug, a.owner_id, r.value, r.comment FROM agent_ratings r JOIN agents a ON a.pk = r.agent_pk
                                  WHERE r.user_id = $1 AND r.project = $2""", user["id"], meta["id"])
    return {r["slug"]: {"value": r["value"], "comment": r["comment"]} for r in rows}


async def rate(user: dict, meta: dict, agent: str, value: int, comment: str = "") -> dict:
    if meta.get("owner") != str(user["id"]):
        raise PermissionError("Only the project's owner can rate the agents that worked on it.")
    if agent not in {e.get("agent") for e in meta.get("stats") or [] if e.get("status") == "done"}:
        raise ValueError("Rate an agent once it has finished work on this project.")
    pk = await agent_pk_for(meta.get("owner"), agent)
    if pk is None:
        raise KeyError(agent)
    comment = " ".join(str(comment or "").split())
    if len(comment) > 500:
        raise ValueError("Keep the comment under 500 characters.")
    if value == 0:
        await db.pool.execute("DELETE FROM agent_ratings WHERE agent_pk = $1 AND user_id = $2 AND project = $3", pk, user["id"], meta["id"])
        return {"agent": agent, "value": 0, "comment": ""}
    if value not in (-1, 1):
        raise ValueError("A rating is a thumbs up or a thumbs down.")
    await db.pool.execute("""INSERT INTO agent_ratings (agent_pk, user_id, project, value, comment) VALUES ($1, $2, $3, $4, $5)
                             ON CONFLICT (agent_pk, user_id, project) DO UPDATE SET value = $4, comment = $5, updated_at = now()""",
                          pk, user["id"], meta["id"], value, comment)
    return {"agent": agent, "value": value, "comment": comment}
