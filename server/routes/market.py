"""The marketplace API: catalogue, profiles, hiring, buying, publishing, withdrawal and review, the ratings of a
project's agents, and the creators' earnings and payouts (accounts/sales.py)."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from accounts import plans
from accounts import sales
from core import limits
from crew import market

from state import _all_metas, _has_preview, load_meta
from web import _limited, current_user, require_admin
from routes.agents import sees_instructions, without_instructions

router = APIRouter()


async def _market_call(call):
    try:
        return await call
    except KeyError:
        raise HTTPException(404, "This isn't in the marketplace (it may have been withdrawn).")
    except market.NeedsPurchase as e:
        # 402: the page offers to buy it (kind, pk and price say what)
        raise HTTPException(402, str(e), headers={"X-Buy": f"{e.kind}:{e.pk}:{e.price:.2f}"})
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
    return await _market_call(market.publish(current_user(request), kind, str(body.get("id") or ""), str(body.get("category") or ""),
                                             body.get("price") or 0))


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


# ---------------------------------------------------------------- buying (sales.py)

def _member(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Log in first.")
    if user["role"] == "unconfirmed":
        raise HTTPException(403, "Confirm your email first.")
    return user


async def _buy(request: Request, kind: str, pk: int):
    user = _member(request)
    _limited(limits.CHECKOUTS, request, "payment attempts", per_member=True)
    return await _market_call(sales.buy(user, kind, _market_pk(pk), str(request.base_url)))


@router.post("/api/market/agents/{pk}/buy")
async def market_buy_agent(request: Request, pk: int):
    """Pay once for a paid agent: the answer's `approve` is PayPal's page ({owned: true} when it's yours already)."""
    return await _buy(request, "agent", pk)


@router.post("/api/market/teams/{pk}/buy")
async def market_buy_team(request: Request, pk: int):
    return await _buy(request, "team", pk)


@router.get("/market/paypal/return")
async def market_paypal_return(request: Request, token: str = ""):
    """PayPal sends the buyer back here (token = the order id): capture it, copy the agent or team, show its profile."""
    user = _member(request)
    try:
        state, p = await sales.confirm_return(user, token)
    except sales.billing.BillingError:
        state, p = "error", None
    if not p:
        return RedirectResponse(f"/hire?paid={state}", status_code=303)
    return RedirectResponse(f"/hire/{'agents' if p['kind'] == 'agent' else 'teams'}/{p['item_pk']}?paid={state}", status_code=303)


# ---------------------------------------------------------------- earnings (the creator's dashboard) and payouts

@router.get("/api/earnings")
async def get_earnings(request: Request, days: int = 90):
    return await sales.earnings(_member(request), days)


@router.put("/api/earnings/payout-email")
async def put_payout_email(request: Request, body: dict):
    return {"payout_email": await sales.set_payout_email(_member(request), str(body.get("email") or ""))}


@router.post("/api/earnings/payout")
async def ask_payout(request: Request):
    return await sales.request_payout(_member(request))


@router.get("/api/admin/payouts")
async def admin_payouts(request: Request):
    require_admin(request)
    return await sales.payout_queue()


@router.post("/api/admin/payouts/{payout_id}")
async def admin_decide_payout(request: Request, payout_id: int, body: dict):
    require_admin(request)
    if not 0 < payout_id <= PK_MAX:
        raise HTTPException(404, "There's no such payout.")
    try:
        return await sales.decide_payout(payout_id, str(body.get("decision") or ""), str(body.get("reference") or ""),
                                         str(body.get("note") or ""))
    except KeyError:
        raise HTTPException(404, "That payout was already decided, or doesn't exist.")
