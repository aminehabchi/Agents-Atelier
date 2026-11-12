"""Selling agents and teams in the marketplace: one-time PayPal payments, the 70/30 split, earnings and payouts.

Who
  Sellers   members on a plan with "publish" (Agency) set a price when they publish an agent or a team (market.py).
            When a seller leaves that plan their paid listings can't be bought until they come back.
  Buyers    every member, Starter included. Admins hire paid items without paying.

The flow (PayPal Orders v2, intent CAPTURE)
  1. Buy on the marketplace → POST /api/market/{agents|teams}/{pk}/buy: the server creates an order for the price,
     records a purchase 'created' with the split worked out, and answers with PayPal's approval page.
  2. The buyer pays and comes back to /market/paypal/return?token=<order id>: the server captures the order, checks the
     amount, marks the purchase 'completed' and copies the agent (locked) or the team into the buyer's own.
  3. PayPal's webhooks cover a buyer who never comes back (CHECKOUT.ORDER.APPROVED → capture,
     PAYMENT.CAPTURE.COMPLETED → complete) and refunds (PAYMENT.CAPTURE.REFUNDED / REVERSED → 'refunded', and the
     creator's share comes off their balance).

The split
  CREATOR_SHARE (70%) of each completed sale is the creator's; the rest is the platform's. A creator's balance is their
  completed sales' share minus their payouts (asked for or paid). They ask for a payout to their PayPal email from the
  Earnings dashboard (at least PAYOUT_MIN); an admin sends the money from PayPal and marks it paid, with its reference.
"""

import logging
import re
import time
import urllib.request
from decimal import ROUND_HALF_UP, Decimal

from accounts import billing
from accounts import plans
from core import config
from core import db
from crew import market

log = logging.getLogger("agents-atelier.sales")

CREATOR_SHARE = Decimal("0.70")
PAYOUT_MIN = 10.0                  # USD: the least a creator can ask to be paid out
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
CENT = Decimal("0.01")


class SaleError(billing.BillingError):
    pass


def enabled() -> bool:
    """One-time payments only need PayPal's app credentials (the subscription plans are billing.py's business)."""
    return bool(config.PAYPAL_CLIENT_ID and config.PAYPAL_CLIENT_SECRET)


def split(price: float) -> tuple[Decimal, Decimal, Decimal]:
    """(amount, creator's share, platform's share), to the cent; the two shares always add up to the amount."""
    amount = Decimal(str(price)).quantize(CENT, ROUND_HALF_UP)
    creator = (amount * CREATOR_SHARE).quantize(CENT, ROUND_HALF_UP)
    return amount, creator, amount - creator


async def _item(kind: str, pk: int):
    table = "agents" if kind == "agent" else "teams"
    r = await db.pool.fetchrow(f"""SELECT x.pk, x.name, x.price, x.listing, x.owner_id, u.plan AS seller_plan, u.role AS seller_role
                                   FROM {table} x LEFT JOIN users u ON u.id = x.owner_id WHERE x.pk = $1""", pk)
    if r is None or r["owner_id"] is None or r["listing"] != "listed":
        raise KeyError(pk)
    return r


# ------------------------------------------------------------ buying

async def buy(user: dict, kind: str, pk: int, base_url: str) -> dict:
    """Start paying for a paid agent or team. {approve: PayPal's page} or {owned: True} when it's already theirs."""
    r = await _item(kind, pk)
    price = float(r["price"] or 0)
    if not price:
        raise SaleError("This one is free: hire it.")
    if r["owner_id"] == user["id"]:
        raise SaleError("It's yours already.")
    if (kind, pk) in await market.owned(user) or market._is_admin(user):
        return {"owned": True}
    if not plans.of({"plan": r["seller_plan"], "role": r["seller_role"]}).get("publish"):
        raise SaleError("This one can't be bought right now: its creator's plan doesn't sell in the marketplace any more.", 409)
    if not enabled():
        raise SaleError("Online payment isn't set up on this server, so paid agents can't be bought yet.", 404)
    amount, creator, platform = split(price)
    base = config.PUBLIC_URL or base_url.rstrip("/")
    back = f"{base}/hire/{'agents' if kind == 'agent' else 'teams'}/{pk}"
    order = await billing.paypal("POST", "/v2/checkout/orders", {
        "intent": "CAPTURE",
        "purchase_units": [{"reference_id": f"{kind}-{pk}", "custom_id": f"{user['id']}:{kind}:{pk}",
                            "description": f"Agents Atelier: {r['name']}"[:127],
                            "amount": {"currency_code": "USD", "value": f"{amount:.2f}"}}],
        "application_context": {"brand_name": "Agents Atelier", "user_action": "PAY_NOW", "shipping_preference": "NO_SHIPPING",
                                "locale": {"fr": "fr-FR"}.get(user.get("language") or "", "en-US"),
                                "return_url": f"{base}/market/paypal/return", "cancel_url": f"{back}?paid=cancelled"}})
    approve = next((l["href"] for l in order.get("links") or [] if l.get("rel") in ("approve", "payer-action")), None)
    if not order.get("id") or not approve:
        raise SaleError("PayPal didn't return a payment page. Try again.", 502)
    await db.pool.execute("""INSERT INTO market_purchases (id, buyer_id, seller_id, kind, item_pk, item_name, amount, seller_share,
                                                           platform_share, created)
                             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
                          order["id"], user["id"], r["owner_id"], kind, pk, r["name"], amount, creator, platform, time.time())
    return {"approve": approve, "id": order["id"]}


def _capture_of(order: dict) -> dict:
    for unit in order.get("purchase_units") or []:
        for c in ((unit or {}).get("payments") or {}).get("captures") or []:
            if isinstance(c, dict):
                return c
    return {}


async def capture(order_id: str) -> dict | None:
    """Capture an approved order at PayPal (or read it, when it was captured already) and complete the purchase."""
    oid = urllib.request.quote(order_id, safe="")
    order = await billing.paypal("POST", f"/v2/checkout/orders/{oid}/capture", {}, ok=(200, 201, 422))
    if order.get("status") != "COMPLETED":                  # 422: already captured (by the webhook or a second click)
        order = await billing.paypal("GET", f"/v2/checkout/orders/{oid}")
    c = _capture_of(order)
    if order.get("status") != "COMPLETED" or c.get("status") != "COMPLETED":
        return None
    return await complete(order_id, c)


async def complete(order_id: str, cap: dict) -> dict | None:
    """Mark a purchase paid (once) and give the buyer the agent or team. Returns the purchase row when it was completed."""
    p = await db.pool.fetchrow("SELECT * FROM market_purchases WHERE id = $1", order_id)
    if p is None:
        log.warning("PayPal order %s isn't a marketplace purchase", order_id)
        return None
    amount = cap.get("amount") if isinstance(cap.get("amount"), dict) else {}
    try:
        paid = Decimal(str(amount.get("value") or "0")).quantize(CENT)
    except ArithmeticError:
        paid = Decimal(0)
    if paid != p["amount"] or str(amount.get("currency_code") or "USD") != p["currency"]:
        log.error("PayPal order %s paid %s %s, expected %s %s: not completed", order_id, paid, amount.get("currency_code"),
                  p["amount"], p["currency"])
        return None
    row = await db.pool.fetchrow("""UPDATE market_purchases SET status = 'completed', completed = $2, capture_id = $3
                                    WHERE id = $1 AND status = 'created' RETURNING *""", order_id, time.time(), str(cap.get("id") or ""))
    if row is None:
        return dict(p) if p["status"] == "completed" else None
    await deliver(row)
    log.info("Marketplace sale %s: %s %s for %s (creator %s, platform %s)", order_id, row["kind"], row["item_pk"], row["amount"],
             row["seller_share"], row["platform_share"])
    return dict(row)


async def deliver(p) -> dict | None:
    """Copy what was bought into the buyer's agents or teams."""
    buyer = await db.pool.fetchrow("SELECT * FROM users WHERE id = $1", p["buyer_id"]) if p["buyer_id"] else None
    if not buyer:
        return None
    try:
        hire = market.hire_agent if p["kind"] == "agent" else market.hire_team
        return await hire(dict(buyer), p["item_pk"], paid=True)
    except KeyError:
        log.warning("Bought %s %s was deleted before it could be copied", p["kind"], p["item_pk"])
        return None


async def confirm_return(user: dict, order_id: str) -> tuple[str, dict | None]:
    """The buyer is back from PayPal: ('paid' | 'pending' | 'cancelled', the purchase)."""
    if not enabled() or not order_id or len(order_id) > 64:
        return "cancelled", None
    p = await db.pool.fetchrow("SELECT * FROM market_purchases WHERE id = $1", order_id)
    if p is None or p["buyer_id"] != user["id"]:
        return "cancelled", None
    if p["status"] == "completed":
        return "paid", dict(p)
    done = await capture(order_id)
    return ("paid", done) if done else ("pending", dict(p))


async def webhook(kind: str, res: dict) -> bool:
    """The marketplace's PayPal events (billing.webhook hands them over). True when one was handled."""
    if kind == "CHECKOUT.ORDER.APPROVED":
        if await db.pool.fetchval("SELECT 1 FROM market_purchases WHERE id = $1 AND status = 'created'", str(res.get("id") or "")):
            await capture(str(res["id"]))
            return True
        return False
    if kind == "PAYMENT.CAPTURE.COMPLETED":
        order_id = str(((res.get("supplementary_data") or {}).get("related_ids") or {}).get("order_id") or "")
        if order_id and await db.pool.fetchval("SELECT 1 FROM market_purchases WHERE id = $1", order_id):
            await complete(order_id, res)
            return True
        return False
    if kind in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED"):
        capture_id = str(res.get("id") or "")
        if kind == "PAYMENT.CAPTURE.REFUNDED":       # the resource is the refund; its "up" link is the capture
            up = next((l.get("href") for l in res.get("links") or [] if isinstance(l, dict) and l.get("rel") == "up"), "")
            capture_id = str(up or "").rstrip("/").rsplit("/", 1)[-1] or capture_id
        done = await db.pool.fetchval("""UPDATE market_purchases SET status = 'refunded' WHERE capture_id = $1 AND capture_id <> ''
                                         AND status = 'completed' RETURNING id""", capture_id)
        if done:
            log.info("Marketplace sale %s was %s", done, "refunded" if kind.endswith("REFUNDED") else "reversed")
        return bool(done)
    return False


# ------------------------------------------------------------ the creator's earnings

def _f(v) -> float:
    return float(v or 0)


async def balance(user_id) -> float:
    earned = await db.pool.fetchval("SELECT coalesce(sum(seller_share), 0) FROM market_purchases WHERE seller_id = $1 AND status = 'completed'",
                                    user_id)
    out = await db.pool.fetchval("SELECT coalesce(sum(amount), 0) FROM creator_payouts WHERE user_id = $1 AND status IN ('requested', 'paid')",
                                 user_id)
    return round(_f(earned) - _f(out), 2)


async def earnings(user: dict, days: int = 90) -> dict:
    """The Earnings dashboard: what the member sold and earned, per item and per day, their balance and payouts, and
    what they bought."""
    days = max(7, min(int(days or 90), 365))
    since = time.time() - days * 86400
    sales = await db.pool.fetch("""SELECT p.*, u.name AS buyer_name, u.email AS buyer_email FROM market_purchases p
                                   LEFT JOIN users u ON u.id = p.buyer_id
                                   WHERE p.seller_id = $1 AND p.status IN ('completed', 'refunded') ORDER BY p.completed DESC""", user["id"])
    done = [s for s in sales if s["status"] == "completed"]
    # every paid listing of the member, sold or not, with its sales
    listings = [dict(r, kind="agent") for r in await db.pool.fetch(
        "SELECT pk, name, price, listing FROM agents WHERE owner_id = $1 AND (price > 0 OR pk IN (SELECT item_pk FROM market_purchases WHERE seller_id = $1 AND kind = 'agent'))",
        user["id"])] + [dict(r, kind="team") for r in await db.pool.fetch(
        "SELECT pk, name, price, listing FROM teams WHERE owner_id = $1 AND (price > 0 OR pk IN (SELECT item_pk FROM market_purchases WHERE seller_id = $1 AND kind = 'team'))",
        user["id"])]
    items = {(x["kind"], x["pk"]): {"kind": x["kind"], "pk": x["pk"], "name": x["name"], "price": _f(x["price"]), "listing": x["listing"],
                                    "sales": 0, "refunds": 0, "gross": 0.0, "earned": 0.0, "last": None} for x in listings}
    for s in sales:
        it = items.setdefault((s["kind"], s["item_pk"]), {"kind": s["kind"], "pk": s["item_pk"], "name": s["item_name"], "price": None,
                                                         "listing": "deleted", "sales": 0, "refunds": 0, "gross": 0.0, "earned": 0.0, "last": None})
        if s["status"] == "completed":
            it["sales"] += 1
            it["gross"] += _f(s["amount"])
            it["earned"] += _f(s["seller_share"])
            it["last"] = max(it["last"] or 0, s["completed"] or 0)
        else:
            it["refunds"] += 1
    by_day = {}
    for s in done:
        if (s["completed"] or 0) >= since:
            d = time.strftime("%Y-%m-%d", time.gmtime(s["completed"]))
            day = by_day.setdefault(d, {"date": d, "sales": 0, "earned": 0.0})
            day["sales"] += 1
            day["earned"] = round(day["earned"] + _f(s["seller_share"]), 2)
    payouts = await db.pool.fetch("SELECT * FROM creator_payouts WHERE user_id = $1 ORDER BY requested DESC", user["id"])
    bought = await db.pool.fetch("""SELECT p.id, p.kind, p.item_pk, p.item_name, p.amount, p.status, p.completed, u.name AS seller_name
                                    FROM market_purchases p LEFT JOIN users u ON u.id = p.seller_id
                                    WHERE p.buyer_id = $1 AND p.status IN ('completed', 'refunded') ORDER BY p.completed DESC LIMIT 200""",
                                 user["id"])
    earned = sum(_f(s["seller_share"]) for s in done)
    paid_out = sum(_f(p["amount"]) for p in payouts if p["status"] == "paid")
    asked = sum(_f(p["amount"]) for p in payouts if p["status"] == "requested")
    bal = round(earned - paid_out - asked, 2)
    first = lambda name, email: (name or "").split(" ")[0] or (email or "").split("@")[0] or "A member"
    return {
        "share": float(CREATOR_SHARE), "payout_min": PAYOUT_MIN, "payment": enabled(), "days": days,
        "can_sell": bool(plans.of(user).get("publish")),
        "payout_email": await db.pool.fetchval("SELECT payout_email FROM users WHERE id = $1", user["id"]) or "",
        "totals": {"sales": len(done), "gross": round(sum(_f(s["amount"]) for s in done), 2), "earned": round(earned, 2),
                   "platform": round(sum(_f(s["platform_share"]) for s in done), 2),
                   "refunds": sum(s["status"] == "refunded" for s in sales),
                   "buyers": len({s["buyer_id"] for s in done if s["buyer_id"]}),
                   "period_earned": round(sum(_f(s["seller_share"]) for s in done if (s["completed"] or 0) >= since), 2),
                   "period_sales": sum((s["completed"] or 0) >= since for s in done),
                   "paid_out": round(paid_out, 2), "requested": round(asked, 2), "balance": bal},
        "items": sorted(items.values(), key=lambda i: (-i["earned"], i["name"].lower())),
        "series": sorted(by_day.values(), key=lambda d: d["date"]),
        "recent": [{"id": s["id"], "kind": s["kind"], "pk": s["item_pk"], "name": s["item_name"], "amount": _f(s["amount"]),
                    "earned": _f(s["seller_share"]), "status": s["status"], "ts": s["completed"],
                    "buyer": first(s["buyer_name"], s["buyer_email"])} for s in sales[:50]],
        "payouts": [{"id": p["id"], "amount": _f(p["amount"]), "email": p["paypal_email"], "status": p["status"], "reference": p["reference"],
                     "note": p["note"], "requested": p["requested"], "decided": p["decided"]} for p in payouts],
        "purchases": [{"kind": b["kind"], "pk": b["item_pk"], "name": b["item_name"], "amount": _f(b["amount"]), "status": b["status"],
                       "ts": b["completed"], "by": first(b["seller_name"], "")} for b in bought],
    }


async def set_payout_email(user: dict, email: str) -> str:
    email = str(email or "").strip().lower()
    if email and not EMAIL_RE.match(email):
        raise SaleError("Enter the email address of your PayPal account.")
    await db.pool.execute("UPDATE users SET payout_email = $2 WHERE id = $1", user["id"], email)
    return email


async def request_payout(user: dict) -> dict:
    """Ask for the whole balance to be sent to the member's PayPal email."""
    email = await db.pool.fetchval("SELECT payout_email FROM users WHERE id = $1", user["id"])
    if not email:
        raise SaleError("Add the email address of your PayPal account first.")
    async with db.pool.acquire() as con, con.transaction():
        await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"payout:{user['id']}")     # one request at a time
        if await con.fetchval("SELECT 1 FROM creator_payouts WHERE user_id = $1 AND status = 'requested'", user["id"]):
            raise SaleError("You already asked for a payout. It's sent within a few days.", 409)
        bal = await balance(user["id"])
        if bal < PAYOUT_MIN:
            raise SaleError(f"You can ask for a payout from ${PAYOUT_MIN:.0f}. Your balance is ${max(bal, 0):.2f}.")
        row = await con.fetchrow("""INSERT INTO creator_payouts (user_id, amount, paypal_email, requested) VALUES ($1, $2, $3, $4)
                                    RETURNING id, amount""", user["id"], Decimal(str(bal)).quantize(CENT), email, time.time())
    log.info("%s asked for a payout of $%s", user["email"], row["amount"])
    return {"id": row["id"], "amount": _f(row["amount"])}


# ------------------------------------------------------------ the admins' side

async def payout_queue() -> dict:
    rows = await db.pool.fetch("""SELECT p.*, u.name, u.email FROM creator_payouts p LEFT JOIN users u ON u.id = p.user_id
                                  ORDER BY p.status <> 'requested', p.requested DESC LIMIT 100""")
    return {"items": [{"id": r["id"], "amount": _f(r["amount"]), "paypal_email": r["paypal_email"], "status": r["status"],
                       "reference": r["reference"], "note": r["note"], "requested": r["requested"], "decided": r["decided"],
                       "member": f"{r['name'] or 'A member'} ({r['email'] or 'deleted account'})"} for r in rows]}


async def decide_payout(payout_id: int, decision: str, reference: str = "", note: str = "") -> dict:
    if decision not in ("paid", "rejected"):
        raise SaleError("Mark it paid or rejected.")
    reference = " ".join(str(reference or "").split())[:80]
    note = " ".join(str(note or "").split())[:300]
    if decision == "paid" and not reference:
        raise SaleError("Add the PayPal transaction id you sent it with.")
    row = await db.pool.fetchrow("""UPDATE creator_payouts SET status = $2, reference = $3, note = $4, decided = $5
                                    WHERE id = $1 AND status = 'requested' RETURNING id, status""",
                                 payout_id, decision, reference, note, time.time())
    if row is None:
        raise KeyError(payout_id)
    return {"id": row["id"], "status": row["status"]}


async def summary(since: float) -> dict:
    """Marketplace sales for the revenue analytics: what buyers paid, the creators' 70% and the platform's 30%."""
    r = await db.pool.fetchrow("""SELECT count(*) AS n, coalesce(sum(amount), 0) AS gross, coalesce(sum(seller_share), 0) AS creators,
                                         coalesce(sum(platform_share), 0) AS platform
                                  FROM market_purchases WHERE status = 'completed' AND completed >= $1""", since)
    owed = await db.pool.fetchval("SELECT coalesce(sum(amount), 0) FROM creator_payouts WHERE status = 'requested'")
    return {"sales": r["n"], "gross": _f(r["gross"]), "creators": _f(r["creators"]), "platform": _f(r["platform"]),
            "payouts_waiting": _f(owed)}

