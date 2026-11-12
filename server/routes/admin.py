"""Admins only: analytics, revenue and monitoring, and the list of accounts (role and plan changes)."""

import shutil
import time

from fastapi import APIRouter, Request

from core import config
from core import db
from accounts import auth
from accounts import billing
from accounts import sales
from accounts import collab
from accounts import notify as notices
from accounts import plans
from site_tools import browser_check

from state import CLAUDE, STARTED, is_busy, runs, store
from web import require_admin

router = APIRouter()


@router.get("/api/admin/analytics")
async def analytics(request: Request, days: int = 30):
    """Sign-ups, activity, builds and cost for the last `days` days (UTC), for admins."""
    require_admin(request)
    days = max(7, min(days, 365))
    import datetime as dt
    today = dt.datetime.now(dt.timezone.utc).date()
    span = [today - dt.timedelta(days=days - 1 - i) for i in range(days)]
    since = dt.datetime.combine(span[0], dt.time(), dt.timezone.utc)
    day_of = lambda ts: dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()
    series = lambda counts: [{"date": d.isoformat(), "value": round(counts.get(d, 0), 4)} for d in span]

    users = await db.pool.fetch("SELECT id, email, name, role, created_at, job_role, use_case, referral, language FROM users")
    signups = {}
    for u in users:
        if u["created_at"] >= since:
            signups[u["created_at"].date()] = signups.get(u["created_at"].date(), 0) + 1
    act = {n: await db.pool.fetchval("SELECT count(DISTINCT user_id) FROM sessions WHERE created_at > now() - make_interval(days => $1)", n)
           for n in (1, 7, 30)}
    breakdown = lambda key: sorted(({"label": k or "Not answered", "value": v} for k, v in
                                    __import__("collections").Counter(u[key] for u in users if u["role"] != "unconfirmed").items()),
                                   key=lambda r: (r["label"] == "Not answered", -r["value"]))

    emails = {str(u["id"]): u["email"] for u in users}
    projects_new, runs, cost, failed = {}, {}, {}, {}
    per_user, total = {}, {"projects": 0, "runs": 0, "failed": 0, "cost": 0.0, "seconds": 0.0}
    for m in store.all():
        owner = m.get("owner") or ""
        total["projects"] += 1
        u = per_user.setdefault(owner, {"email": emails.get(owner, "(no owner)"), "projects": 0, "runs": 0, "cost": 0.0, "last": 0})
        u["projects"] += 1
        if m.get("created", 0) >= since.timestamp():
            projects_new[day_of(m["created"])] = projects_new.get(day_of(m["created"]), 0) + 1
        for e in m.get("stats", []):
            c = e.get("cost") or 0
            total["runs"] += 1; total["cost"] += c; total["seconds"] += e.get("seconds") or 0
            u["runs"] += 1; u["cost"] += c; u["last"] = max(u["last"], e.get("ts", 0))
            if e.get("status") != "done":
                total["failed"] += 1
            if e.get("ts", 0) >= since.timestamp():
                d = day_of(e["ts"])
                runs[d] = runs.get(d, 0) + 1
                cost[d] = cost.get(d, 0) + c
                if e.get("status") != "done":
                    failed[d] = failed.get(d, 0) + 1
    top = sorted(per_user.values(), key=lambda r: -r["cost"])[:10]
    for r in top:
        r["cost"] = round(r["cost"], 4)
    confirmed = sum(u["role"] != "unconfirmed" for u in users)
    return {
        "days": days,
        "users": {"total": len(users), "confirmed": confirmed, "unconfirmed": len(users) - confirmed,
                  "admins": sum(u["role"] == "admin" for u in users), "new": sum(signups.values()),
                  "active_1": act[1], "active_7": act[7], "active_30": act[30]},
        "builds": {**{k: round(v, 4) for k, v in total.items()},
                   "runs_in_range": sum(runs.values()), "cost_in_range": round(sum(cost.values()), 4),
                   "failed_in_range": sum(failed.values()), "projects_in_range": sum(projects_new.values())},
        "series": {"signups": series(signups), "projects": series(projects_new), "runs": series(runs), "cost": series(cost)},
        "breakdown": {k: breakdown(k) for k in ("job_role", "use_case", "referral", "language")},
        "top_users": top,
    }


def _day_span(days: int):
    import datetime as dt
    today = dt.datetime.now(dt.timezone.utc).date()
    span = [today - dt.timedelta(days=days - 1 - i) for i in range(days)]
    return span, dt.datetime.combine(span[0], dt.time(), dt.timezone.utc).timestamp()


@router.get("/api/admin/revenue")
async def revenue(request: Request, days: int = 30):
    """What the members' plans are worth per month (a yearly plan counts a twelfth of its yearly price), how it moved, and
    the money PayPal collected when it's set up. Admins and deactivated accounts don't count."""
    require_admin(request)
    import datetime as dt
    days = max(7, min(days, 365))
    span, since = _day_span(days)
    price = {k: v["price"] for k, v in plans.PLANS.items()}
    users = await db.pool.fetch("""SELECT id, email, name, role, plan, billing_period, plan_request, plan_request_at, plan_request_period,
                                          deactivated_at, created_at FROM users""")
    worth = lambda u: plans.monthly_value(u["plan"], u["billing_period"])      # a yearly plan counts a twelfth of its price
    members = [u for u in users if u["role"] == "user" and not u["deactivated_at"]]
    events = await db.pool.fetch("""SELECT e.user_id, e.from_plan, e.to_plan, e.by_admin, e.ts, u.email
                                    FROM plan_events e JOIN users u ON u.id = e.user_id ORDER BY e.ts""")
    counted = {u["id"] for u in members}
    mrr = round(sum(worth(u) for u in members), 2)
    by_plan = [{"id": k, "name": v["name"], "price": v["price"], "price_yearly": v.get("price_yearly"),
                "members": sum(u["plan"] == k for u in members),
                "yearly": sum(u["plan"] == k and u["billing_period"] == "yearly" for u in members),
                "mrr": round(sum(worth(u) for u in members if u["plan"] == k), 2)} for k, v in plans.PLANS.items()]
    paying = sum(u["plan"] != "starter" for u in members)
    pending = [u for u in members if u["plan_request"]]

    # the monthly value on each day: each member's plan as it stood at the end of that day
    def plan_on(uid, end_ts, now_plan):
        mine = [e for e in events if e["user_id"] == uid]
        before = [e for e in mine if e["ts"] <= end_ts]
        if before:
            return before[-1]["to_plan"]
        return mine[0]["from_plan"] if mine else now_plan
    series = []
    for d in span:
        end = dt.datetime.combine(d, dt.time.max, dt.timezone.utc).timestamp()
        # the current plan counts with the member's period (yearly: a twelfth); earlier plans at their monthly price
        series.append({"date": d.isoformat(), "value": round(sum(
            plans.monthly_value(p, u["billing_period"] if p == u["plan"] else "monthly")
            for u in members if u["created_at"].timestamp() <= end for p in [plan_on(u["id"], end, u["plan"])]), 2)})
    moves = [e for e in events if e["ts"] >= since and e["user_id"] in counted]
    up = [e for e in moves if plans.rank(e["to_plan"]) > plans.rank(e["from_plan"])]
    down = [e for e in moves if plans.rank(e["to_plan"]) < plans.rank(e["from_plan"])]
    gained = sum(price.get(e["to_plan"], 0) - price.get(e["from_plan"], 0) for e in up)
    lost = sum(price.get(e["from_plan"], 0) - price.get(e["to_plan"], 0) for e in down)
    return {
        "received": await billing.received(since),
        "marketplace": await sales.summary(since),
        "days": days, "mrr": mrr, "arr": mrr * 12, "members": len(members), "paying": paying,
        "conversion": round(paying / len(members), 4) if members else 0,
        "arpu": round(mrr / len(members), 2) if members else 0,
        "by_plan": by_plan,
        "pending": {"count": len(pending),
                    "mrr": round(sum(plans.monthly_value(u["plan_request"], u["plan_request_period"]) - worth(u) for u in pending), 2),
                    "items": [{"email": u["email"], "from": u["plan"], "to": u["plan_request"], "period": u["plan_request_period"],
                               "at": u["plan_request_at"]} for u in pending]},
        "moves": {"upgrades": len(up), "downgrades": len(down), "gained": gained, "lost": lost, "net": gained - lost},
        "series": series,
        "recent": [{"email": e["email"], "from": e["from_plan"], "to": e["to_plan"], "by_admin": e["by_admin"], "ts": e["ts"]}
                   for e in reversed(events[-20:])],
    }


@router.get("/api/admin/monitoring")
async def monitoring(request: Request, days: int = 30):
    """Is everything working: the server's parts, runs failing, members who can't build, accounts on their way out."""
    require_admin(request)
    days = max(7, min(days, 365))
    span, since = _day_span(days)
    t0 = time.perf_counter()
    try:
        await db.pool.fetchval("SELECT 1")
        db_ms, db_ok = round((time.perf_counter() - t0) * 1000, 1), True
    except Exception:
        db_ms, db_ok = None, False
    disk = shutil.disk_usage(config.STORAGE_DIR)
    import datetime as dt
    day_of = lambda ts: dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()
    statuses, by_agent, fail_days, seconds = {}, {}, {}, []
    errors = []
    for m in store.all():
        run = m.get("run") or {}
        if run.get("status") == "error" and run.get("updated", 0) >= since:
            errors.append({"project": m["name"], "id": m["id"], "error": (run.get("error") or "")[-300:], "ts": run.get("updated"),
                           "agent": run.get("current") or ""})
        for e in m.get("stats", []):
            if e.get("ts", 0) < since:
                continue
            s = e.get("status") or "done"
            statuses[s] = statuses.get(s, 0) + 1
            a = by_agent.setdefault(e.get("agent") or "?", {"agent": e.get("agent") or "?", "runs": 0, "failed": 0, "seconds": 0.0})
            a["runs"] += 1; a["seconds"] += e.get("seconds") or 0
            if s != "done":
                a["failed"] += 1
                fail_days[day_of(e["ts"])] = fail_days.get(day_of(e["ts"]), 0) + 1
            if s == "done" and e.get("seconds"):
                seconds.append(e["seconds"])
    agents_rows = sorted(by_agent.values(), key=lambda a: (-a["failed"], -a["runs"]))[:10]
    for a in agents_rows:
        a["avg_seconds"] = round(a["seconds"] / a["runs"], 1) if a["runs"] else 0
        a["seconds"] = round(a["seconds"], 1)
    users = await db.pool.fetch("SELECT email, role, claude_key, deactivated_at, delete_after FROM users")
    members = [u for u in users if u["role"] == "user" and not u["deactivated_at"]]
    seconds.sort()
    busy = [{"id": m["id"], "name": m["name"]} for m in store.all() if is_busy(m["id"])]
    return {
        "days": days,
        "health": {"database": {"ok": db_ok, "ms": db_ms}, "chrome": browser_check.available(),
                   "claude": bool(shutil.which(CLAUDE)), "keys": config.CLAUDE_KEYS,
                   "disk": {"free": disk.free, "total": disk.total}, "uptime": round(time.time() - STARTED),
                   "busy": busy, "viewers": sum(len(s.get("clients") or ()) for s in runs.values())},
        "runs": {"total": sum(statuses.values()), "statuses": statuses,
                 "failed": sum(v for k, v in statuses.items() if k != "done"),
                 "median_seconds": seconds[len(seconds) // 2] if seconds else 0,
                 "p90_seconds": seconds[int(len(seconds) * 0.9)] if seconds else 0},
        "failures": [{"date": d.isoformat(), "value": fail_days.get(d, 0)} for d in span],
        "agents": agents_rows,
        "errors": sorted(errors, key=lambda e: -(e["ts"] or 0))[:15],
        "accounts": {"members": len(members), "with_key": sum(bool(u["claude_key"]) for u in members),
                     "unconfirmed": sum(u["role"] == "unconfirmed" for u in users),
                     "leaving": [{"email": u["email"], "delete_after": u["delete_after"]}
                                 for u in sorted(users, key=lambda u: u["delete_after"] or 0) if u["delete_after"]]},
    }


@router.get("/api/users")
async def users_list(request: Request):
    require_admin(request)
    return await auth.list_users()


@router.patch("/api/users/{user_id}")
async def users_set_role(request: Request, user_id: str, body: dict):
    """An admin changes a member's role and/or plan."""
    actor = require_admin(request)
    out = None
    if body.get("decline_request"):
        out = await auth.decline_plan_request(actor, user_id)
        await notices.plan_declined(out["id"])
        return out
    if "plan" in body:
        before = await db.pool.fetchval("SELECT plan FROM users WHERE id = $1", collab._uid(user_id)) if user_id else None
        out = await auth.set_plan(actor, user_id, str(body.get("plan") or ""))
        if plans.rank(out["plan"]) > plans.rank(before or plans.DEFAULT):
            await notices.plan_active(out["id"], out["plan"], out.get("billing_period") or "monthly")
    if "role" in body or out is None:
        out = await auth.set_role(actor, user_id, str(body.get("role") or ""))
    await collab.refresh_user(user_id)            # the people this member invited keep or lose access with the plan
    return out
