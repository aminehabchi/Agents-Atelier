"""The marketplace API: catalogue, profiles, hiring, publishing, withdrawal and review, and the ratings of a
project's agents."""

from fastapi import APIRouter, HTTPException, Request

from accounts import plans
from crew import market

from state import _all_metas, _has_preview, load_meta
from web import current_user, require_admin
from routes.agents import sees_instructions, without_instructions

router = APIRouter()


async def _market_call(call):
    try:
        return await call
    except KeyError:
        raise HTTPException(404, "This isn't in the marketplace (it may have been withdrawn).")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/market")
async def market_catalogue(request: Request):
    return await market.catalogue(current_user(request), _all_metas())


PK_MAX = 2 ** 63 - 1       # the largest id Postgres stores (bigint)


def _market_pk(pk: int) -> int:
    """An id from the URL: one that can't exist is simply not in the marketplace (instead of a database error)."""
    if not 0 < pk <= PK_MAX:
        raise HTTPException(404, "This isn't in the marketplace (it may have been withdrawn).")
    return pk


@router.get("/api/market/agents/{pk}")
async def market_agent(request: Request, pk: int):
    profile = await _market_call(market.agent_profile(current_user(request), _market_pk(pk), _all_metas(), _has_preview))
    return profile if sees_instructions(current_user(request)) else without_instructions(profile)


@router.get("/api/market/teams/{pk}")
async def market_team(request: Request, pk: int):
    profile = await _market_call(market.team_profile(current_user(request), _market_pk(pk), _all_metas()))
    if not sees_instructions(current_user(request)) and isinstance(profile, dict):
        profile = {**profile, "instructions_locked": True,
                   "agents": [without_instructions(a) if isinstance(a, dict) else a for a in profile.get("agents") or []]}
    return profile


@router.post("/api/market/agents/{pk}/hire")
async def market_hire_agent(request: Request, pk: int):
    return await _market_call(market.hire_agent(current_user(request), _market_pk(pk)))


@router.post("/api/market/teams/{pk}/hire")
async def market_hire_team(request: Request, pk: int):
    return await _market_call(market.hire_team(current_user(request), _market_pk(pk)))


@router.post("/api/market/publish")
async def market_publish(request: Request, body: dict):
    plans.need(current_user(request), "publish", "Publishing agents and teams to the marketplace comes with the Agency plan.")
    kind = "team" if body.get("kind") == "team" else "agent"
    return await _market_call(market.publish(current_user(request), kind, str(body.get("id") or ""), str(body.get("category") or "")))


@router.post("/api/market/withdraw")
async def market_withdraw(request: Request, body: dict):
    kind = "team" if body.get("kind") == "team" else "agent"
    return await _market_call(market.withdraw(current_user(request), kind, str(body.get("id") or "")))


@router.get("/api/market/pending")
async def market_pending(request: Request):
    require_admin(request)
    return await market.pending()


@router.post("/api/market/review")
async def market_review(request: Request, body: dict):
    require_admin(request)
    kind = "team" if body.get("kind") == "team" else "agent"
    try:
        pk = _market_pk(int(body.get("pk")))
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(400, "Which one? Send its pk.")
    return await _market_call(market.review(kind, pk, str(body.get("decision") or ""), str(body.get("note") or "")))


@router.get("/api/projects/{pid}/ratings")
async def get_ratings(request: Request, pid: str):
    return await market.project_ratings(current_user(request), load_meta(pid))


@router.put("/api/projects/{pid}/ratings")
async def put_rating(request: Request, pid: str, body: dict):
    try:
        value = int(body.get("value") or 0)
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(400, "A rating is a thumbs up or a thumbs down.")
    return await _market_call(market.rate(current_user(request), load_meta(pid), str(body.get("agent") or ""), value,
                                          str(body.get("comment") or "")))
