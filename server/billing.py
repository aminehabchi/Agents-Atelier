"""Online payment: members pay for Pro and Agency with a PayPal subscription (monthly, billed by PayPal).

Turned on when PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET and the two plan ids are set (see .env.example); otherwise an
upgrade stays a request an admin approves (auth.change_my_plan), as before.

The flow
  1. Upgrade on /profile → POST /api/billing/paypal/subscribe {plan}: the server creates a subscription at PayPal for
     that plan, with the member's id as custom_id, and answers with PayPal's approval page.
  2. The member approves at PayPal and comes back to /billing/paypal/return?subscription_id=…: the server asks PayPal
     for that subscription, checks it's this member's and active, and switches the plan.
  3. PayPal's webhooks (/api/billing/paypal/webhook, signature checked with PayPal) keep it right afterwards: renewals
     are recorded as payments; a cancelled, suspended or expired subscription moves the member back to Starter.
  4. Moving to Starter on /profile cancels the subscription at PayPal. Moving between Pro and Agency starts a new
     subscription; when it's active, the old one is cancelled.

The AI itself is never billed here: it runs on the member's own Claude key.
"""

import asyncio
import base64
import json
import logging
import time
import urllib.error
import urllib.request
import uuid

import auth
import config
import notify
import db
import plans

log = logging.getLogger("agents-atelier.billing")

PLAN_IDS = {"pro": config.PAYPAL_PLAN_PRO, "agency": config.PAYPAL_PLAN_AGENCY}
YEARLY_IDS = {"pro": config.PAYPAL_PLAN_PRO_YEARLY, "agency": config.PAYPAL_PLAN_AGENCY_YEARLY}   # optional
ACTIVE = {"ACTIVE"}
ENDED = {"CANCELLED", "SUSPENDED", "EXPIRED"}
_token: dict = {"value": "", "until": 0.0}


class BillingError(Exception):
    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.status = status


def enabled() -> bool:
    return bool(config.PAYPAL_CLIENT_ID and config.PAYPAL_CLIENT_SECRET and all(PLAN_IDS.values()))


def yearly_enabled() -> bool:
    return enabled() and all(YEARLY_IDS.values())


def plan_of_paypal(plan_id: str) -> tuple[str | None, str]:
    """(our plan, monthly|yearly) for a PayPal plan id."""
    for ids, period in ((PLAN_IDS, "monthly"), (YEARLY_IDS, "yearly")):
        found = next((k for k, v in ids.items() if v and v == plan_id), None)
        if found:
            return found, period
    return None, "monthly"


# ------------------------------------------------------------ PayPal's REST API

def _http(method: str, path: str, body=None, headers=None, timeout: int = 30) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(config.PAYPAL_API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read()
            return r.status, json.loads(text) if text else {}
    except urllib.error.HTTPError as e:
        with e:
            text = e.read()
        try:
            return e.code, json.loads(text) if text else {}
        except ValueError:
            return e.code, {"message": text.decode(errors="replace")[:300]}


async def _access_token() -> str:
    if _token["value"] and _token["until"] > time.time() + 60:
        return _token["value"]
    basic = base64.b64encode(f"{config.PAYPAL_CLIENT_ID}:{config.PAYPAL_CLIENT_SECRET}".encode()).decode()

    def get():
        req = urllib.request.Request(config.PAYPAL_API + "/v1/oauth2/token", data=b"grant_type=client_credentials", method="POST",
                                     headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded",
                                              "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    try:
        data = await asyncio.to_thread(get)
        _token.update(value=str(data["access_token"]), until=time.time() + int(data.get("expires_in") or 300))
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.error("PayPal login failed: %s", e)
        raise BillingError("PayPal can't be reached right now. Try again in a moment.", 502)
    except (KeyError, TypeError, AttributeError) as e:
        log.error("PayPal login answered without a token: %s", e)
        raise BillingError("PayPal refused the server's login. Try again later, or contact the administrator.", 502)
    return _token["value"]


async def paypal(method: str, path: str, body=None, ok=(200, 201, 204)) -> dict:
    token = await _access_token()
    try:
        status, data = await asyncio.to_thread(_http, method, path, body, {"Authorization": f"Bearer {token}",
                                                                            "PayPal-Request-Id": str(uuid.uuid4())})
    except (urllib.error.URLError, OSError, ValueError) as e:     # ValueError: an answer that isn't JSON
        log.error("PayPal %s %s failed: %s", method, path, e)
        raise BillingError("PayPal can't be reached right now. Try again in a moment.", 502)
    if status not in ok:
        log.error("PayPal %s %s answered %s: %s", method, path, status, str(data)[:500])
        raise BillingError("PayPal refused the request. Try again, or contact the administrator.", 502)
    return data if isinstance(data, dict) else {}


# ------------------------------------------------------------ the member's side

async def subscribe(user: dict, plan: str, base_url: str, period: str = "monthly") -> dict:
    """Start a subscription; the member approves it on PayPal's page (the `approve` link)."""
    if not enabled():
        raise BillingError("Online payment isn't set up on this server.", 404)
    if user["role"] == "admin":
        raise BillingError("Admins already have every feature.")
    if plan not in PLAN_IDS:
        raise BillingError("Pick Pro or Agency.")
    if period not in plans.PERIODS:
        raise BillingError("Pick monthly or yearly.")
    if period == "yearly" and not yearly_enabled():
        raise BillingError("Paying by the year isn't available on this server yet. Pick monthly.")
    if (plan == (user.get("plan") or plans.DEFAULT) and user.get("billing_subscription")
            and period == (user.get("billing_period") or "monthly")):
        raise BillingError(f"You're already on {plans.PLANS[plan]['name']}, paid {period}.")
    base = config.PUBLIC_URL or base_url.rstrip("/")
    sub = await paypal("POST", "/v1/billing/subscriptions", {
        "plan_id": (YEARLY_IDS if period == "yearly" else PLAN_IDS)[plan], "custom_id": str(user["id"]),
        "application_context": {"brand_name": "Agents Atelier", "user_action": "SUBSCRIBE_NOW", "shipping_preference": "NO_SHIPPING",
                                "locale": {"fr": "fr-FR", "ary": "fr-MA"}.get(user.get("language") or "", "en-US"),
                                "return_url": f"{base}/billing/paypal/return", "cancel_url": f"{base}/profile?paid=cancelled#plan"}})
    approve = next((l["href"] for l in sub.get("links") or [] if l.get("rel") == "approve"), None)
    if not sub.get("id") or not approve:
        raise BillingError("PayPal didn't return a payment page. Try again.", 502)
    await _save_subscription(sub["id"], user["id"], plan, sub.get("status") or "APPROVAL_PENDING", period=period)
    return {"approve": approve, "id": sub["id"]}


async def confirm_return(user: dict, subscription_id: str) -> str:
    """The member is back from PayPal: 'active' (plan switched), 'pending' (PayPal is still confirming) or 'cancelled'."""
    if not enabled() or not subscription_id or len(subscription_id) > 64:
        return "cancelled"
    sub = await paypal("GET", f"/v1/billing/subscriptions/{urllib.request.quote(subscription_id, safe='')}")
    if sub.get("custom_id") != str(user["id"]):
        log.warning("PayPal subscription %s isn't %s's", subscription_id, user["email"])
        return "cancelled"
    await apply(sub)
    status = sub.get("status") or ""
    return "active" if status in ACTIVE else "pending" if status in ("APPROVED", "APPROVAL_PENDING") else "cancelled"


async def cancel(user: dict, reason: str = "Moved to another plan in Agents Atelier"):
    """Stop the member's current subscription at PayPal (moving to Starter)."""
    sub_id = user.get("billing_subscription") or ""
    if not sub_id or not enabled():
        return
    await _cancel_at_paypal(sub_id, reason)
    await db.pool.execute("UPDATE users SET billing_subscription = '' WHERE id = $1", user["id"])


async def _cancel_at_paypal(sub_id: str, reason: str):
    try:
        await paypal("POST", f"/v1/billing/subscriptions/{urllib.request.quote(sub_id, safe='')}/cancel", {"reason": reason[:120]},
                     ok=(200, 204, 422))      # 422: already cancelled
    except BillingError:
        log.error("Couldn't cancel PayPal subscription %s: cancel it in the PayPal dashboard", sub_id)
        raise
    await db.pool.execute("UPDATE billing_subscriptions SET status = 'CANCELLED', updated = $2 WHERE id = $1", sub_id, time.time())


# ------------------------------------------------------------ keeping plans in step with PayPal

async def _save_subscription(sub_id: str, user_id, plan: str, status: str, next_billing: float | None = None, period: str = "monthly"):
    now = time.time()
    await db.pool.execute("""INSERT INTO billing_subscriptions (id, user_id, plan, status, created, updated, next_billing, period)
                             VALUES ($1, $2, $3, $4, $5, $5, $6, $7)
                             ON CONFLICT (id) DO UPDATE SET status = $4, updated = $5,
                               next_billing = coalesce($6, billing_subscriptions.next_billing), period = $7""",
                          sub_id, uuid.UUID(str(user_id)) if user_id else None, plan, status, now, next_billing, period)


def _ts(value) -> float | None:
    import datetime as dt
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() if value else None
    except ValueError:
        return None


async def _set_plan(user_id, plan: str, sub_id: str, period: str = "monthly") -> dict | None:
    before = await db.pool.fetchrow("SELECT plan, role FROM users WHERE id = $1", user_id)
    if not before:
        return None
    row = await db.pool.fetchrow("""UPDATE users SET plan = $2, billing_subscription = $3, billing_period = $4, plan_request = '',
                                    plan_request_at = NULL WHERE id = $1 RETURNING *""", user_id, plan, sub_id, period)
    await auth._plan_event(user_id, before["plan"], plan, by_admin=False)
    return dict(row)


async def apply(sub: dict) -> dict | None:
    """Bring a member's plan in line with one of their PayPal subscriptions. Returns the updated user when it changed."""
    sub_id, status = sub.get("id") or "", sub.get("status") or ""
    plan, period = plan_of_paypal(sub.get("plan_id") or "")
    row = await db.pool.fetchrow("SELECT user_id, plan, period FROM billing_subscriptions WHERE id = $1", sub_id)
    try:
        user_id = uuid.UUID(sub.get("custom_id") or (str(row["user_id"]) if row and row["user_id"] else ""))
    except ValueError:
        log.warning("PayPal subscription %s has no member", sub_id)
        return None
    if not plan and row:
        plan, period = row["plan"], row["period"]
    if not plan:
        log.warning("PayPal subscription %s is for an unknown plan %s", sub_id, sub.get("plan_id"))
        return None
    await _save_subscription(sub_id, user_id, plan, status, _ts((sub.get("billing_info") or {}).get("next_billing_time")), period)
    user = await db.pool.fetchrow("SELECT id, email, plan, billing_subscription FROM users WHERE id = $1", user_id)
    if not user:
        return None
    if status in ACTIVE:
        old = user["billing_subscription"]
        if user["plan"] == plan and old == sub_id:
            return None
        changed = await _set_plan(user_id, plan, sub_id, period)
        log.info("%s is on %s (%s) through PayPal (%s)", user["email"], plan, period, sub_id)
        await notify.plan_active(user_id, plan, period)
        if old and old != sub_id:                    # an upgrade or a switch: one subscription at a time
            try:
                await _cancel_at_paypal(old, "Replaced by a new plan")
            except BillingError:
                pass
        return changed
    if status in ENDED and user["billing_subscription"] == sub_id:
        changed = await _set_plan(user_id, plans.DEFAULT, "")
        log.info("%s's PayPal subscription %s is %s: back to Starter", user["email"], sub_id, status.lower())
        await notify.plan_ended(user_id, plan, status)
        return changed
    return None


async def verify_webhook(headers, event: dict) -> bool:
    if not config.PAYPAL_WEBHOOK_ID:
        log.error("A PayPal webhook arrived but PAYPAL_WEBHOOK_ID isn't set: ignored")
        return False
    h = {k.lower(): v for k, v in headers.items()}
    body = {"auth_algo": h.get("paypal-auth-algo", ""), "cert_url": h.get("paypal-cert-url", ""),
            "transmission_id": h.get("paypal-transmission-id", ""), "transmission_sig": h.get("paypal-transmission-sig", ""),
            "transmission_time": h.get("paypal-transmission-time", ""), "webhook_id": config.PAYPAL_WEBHOOK_ID, "webhook_event": event}
    if not all(body.values()):
        return False
    try:
        r = await paypal("POST", "/v1/notifications/verify-webhook-signature", body)
    except BillingError:
        return False
    return r.get("verification_status") == "SUCCESS"


async def webhook(headers, raw: bytes) -> dict:
    """One event from PayPal. Returns what was done (for the log); unknown events are acknowledged and ignored."""
    try:
        event = json.loads(raw)
    except ValueError:
        raise BillingError("Not JSON.")
    if not isinstance(event, dict):
        raise BillingError("A webhook event is a JSON object.")
    if not enabled() or not await verify_webhook(headers, event):
        raise BillingError("This webhook couldn't be verified.", 401)
    event_id, kind, res = str(event.get("id") or ""), str(event.get("event_type") or ""), event.get("resource") or {}
    res = res if isinstance(res, dict) else {}
    first = await db.pool.fetchval("INSERT INTO billing_events (id, type, ts) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING RETURNING id",
                                   event_id, kind, time.time())
    if not first:
        return {"duplicate": True}
    changed = None
    try:
        if kind == "BILLING.SUBSCRIPTION.PAYMENT.FAILED":
            row = await db.pool.fetchrow("SELECT user_id, plan FROM billing_subscriptions WHERE id = $1", str(res.get("id") or ""))
            if row and row["user_id"]:
                await notify.payment_failed(row["user_id"], row["plan"])
        elif kind.startswith("BILLING.SUBSCRIPTION."):
            changed = await apply(res)
        elif kind in ("PAYMENT.SALE.COMPLETED", "PAYMENT.SALE.REFUNDED", "PAYMENT.SALE.REVERSED"):
            await record_payment(kind, res)
    except Exception:
        # not handled after all: forget it, so PayPal's next delivery of this event is handled instead of skipped
        log.exception("PayPal webhook %s (%s) failed; PayPal will send it again", event_id, kind)
        try:
            await db.pool.execute("DELETE FROM billing_events WHERE id = $1", event_id)
        except Exception:
            log.exception("Couldn't forget PayPal webhook %s: handle it by hand", event_id)
        raise
    return {"type": kind, "plan_changed": bool(changed), "user": str(changed["id"]) if changed else None}


async def record_payment(kind: str, res: dict):
    amount = res.get("amount") if isinstance(res.get("amount"), dict) else {}
    try:
        value = float(amount.get("total") or amount.get("value") or 0)
    except (TypeError, ValueError):
        value = 0.0
    status = {"PAYMENT.SALE.COMPLETED": "completed", "PAYMENT.SALE.REFUNDED": "refunded", "PAYMENT.SALE.REVERSED": "reversed"}[kind]
    if status != "completed":
        value = -abs(value)
    sub_id = res.get("billing_agreement_id") or ""
    user_id = await db.pool.fetchval("SELECT user_id FROM billing_subscriptions WHERE id = $1", sub_id) if sub_id else None
    await db.pool.execute("""INSERT INTO billing_payments (id, subscription_id, user_id, amount, currency, status, ts)
                             VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (id) DO NOTHING""",
                          str(res.get("id") or uuid.uuid4()) + ("" if status == "completed" else f":{status}"), sub_id or None,
                          user_id, round(value, 2), str(amount.get("currency") or amount.get("currency_code") or "USD")[:3], status,
                          _ts(res.get("create_time")) or time.time())


async def received(since: float) -> dict:
    """Money received through PayPal since a time, for the revenue analytics."""
    rows = await db.pool.fetch("""SELECT p.amount, p.currency, p.status, p.ts, u.email FROM billing_payments p
                                  LEFT JOIN users u ON u.id = p.user_id WHERE p.ts >= $1 ORDER BY p.ts DESC""", since)
    return {"enabled": enabled(), "total": round(sum(float(r["amount"]) for r in rows), 2), "count": sum(r["status"] == "completed" for r in rows),
            "recent": [{"email": r["email"] or "", "amount": float(r["amount"]), "currency": r["currency"], "status": r["status"], "ts": r["ts"]}
                       for r in rows[:10]]}


# ------------------------------------------------------------ manage.py paypal-setup

async def setup_plans() -> dict:
    """Create the PayPal product and a monthly and a yearly plan per paid plan in plans.py. Returns the ids for .env."""
    product = await paypal("POST", "/v1/catalogs/products", {"name": "Agents Atelier", "type": "SERVICE", "category": "SOFTWARE",
                                                             "description": "AI agents that build websites"})
    out = {}
    for key in ("pro", "agency"):
        p = plans.PLANS[key]
        for unit, price, suffix, label in (("MONTH", p["price"], "", ""), ("YEAR", p["price_yearly"], "_YEARLY", " (yearly)")):
            plan = await paypal("POST", "/v1/billing/plans", {
                "product_id": product["id"], "name": f"Agents Atelier {p['name']}{label}", "status": "ACTIVE",
                "billing_cycles": [{"frequency": {"interval_unit": unit, "interval_count": 1}, "tenure_type": "REGULAR", "sequence": 1,
                                    "total_cycles": 0, "pricing_scheme": {"fixed_price": {"value": f"{price:.2f}", "currency_code": "USD"}}}],
                "payment_preferences": {"auto_bill_outstanding": True, "payment_failure_threshold": 2}})
            out[f"PAYPAL_PLAN_{key.upper()}{suffix}"] = plan["id"]
    return out
