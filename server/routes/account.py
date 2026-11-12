"""Your own account: sign-up, login (also with Google or GitHub), email codes, password, sessions, profile and
preferences, plans and PayPal billing, the Claude API key and spending limit, deletion and the data export."""

import asyncio
import json
import re
import secrets
import shutil
import time
import urllib.parse
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from core import config
from core import db
from core import limits
from core import storage
from accounts import auth
from accounts import billing
from accounts import collab
from accounts import keys
from accounts import notify as notices
from accounts import oauth
from accounts import plans
from crew import team_files
from crew.team_files import load_agents

from state import is_busy, log, site_dir, store
from web import _limited, explain_exception
from project_data import BRAND_DIR, _brand_logo, _remove_project_files, brand_kit

router = APIRouter()


@router.get("/api/auth/legal")
async def legal_details():
    """Who runs this service, for the Terms and Privacy pages (set in .env)."""
    return {"name": config.LEGAL_NAME, "email": config.LEGAL_EMAIL, "address": config.LEGAL_ADDRESS,
            "smtp": bool(config.SMTP_HOST)}


# ---------------------------------------------------------------- accounts

def _with_session(user: dict, token: str | None = None) -> JSONResponse:
    res = JSONResponse({"user": auth.public(user)})
    if token:
        res.set_cookie(auth.COOKIE, token, max_age=config.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                       secure=config.COOKIE_SECURE, path="/")
    return res


@router.get("/api/auth/options")
async def auth_options():
    return {**auth.PROFILE_OPTIONS, "email_enabled": bool(config.SMTP_HOST), "oauth": oauth.enabled()}


def _oauth_back(error: str, next_path: str = "/") -> RedirectResponse:
    """Back to the login page with a message (the page shows ?oauth_error=)."""
    q = urllib.parse.urlencode({"oauth_error": error, **({"next": next_path} if next_path != "/" else {})})
    res = RedirectResponse(f"/login?{q}", status_code=303)
    res.delete_cookie(oauth.STATE_COOKIE, path="/api/auth/oauth")
    return res


@router.get("/api/auth/oauth/{provider}/start")
async def oauth_start(request: Request, provider: str, next: str = "/"):
    """Send the browser to Google or GitHub; it comes back to the callback below."""
    try:
        _limited(limits.LOGIN, request, "login attempts")
        url, state = oauth.start(provider, str(request.base_url), next)
    except oauth.OAuthError as e:
        return _oauth_back(str(e), oauth.safe_next(next))
    except HTTPException as e:
        return _oauth_back(e.detail, oauth.safe_next(next))
    res = RedirectResponse(url, status_code=303)
    res.set_cookie(oauth.STATE_COOKIE, state, max_age=oauth.STATE_SECONDS, httponly=True, samesite="lax",
                   secure=config.COOKIE_SECURE, path="/api/auth/oauth")
    return res


@router.get("/api/auth/oauth/{provider}/callback")
async def oauth_callback(request: Request, provider: str, code: str = "", state: str = "", error: str = ""):
    """Back from the provider: check the state, then log in (or create) the account and start a session."""
    saved = oauth.read_state(request.cookies.get(oauth.STATE_COOKIE))
    next_path = oauth.safe_next(saved.get("next"))
    if error:
        return _oauth_back("You cancelled the login, or the provider refused it.", next_path)
    try:
        started = float(saved.get("at") or 0)
    except (TypeError, ValueError, OverflowError):
        started = 0.0
    if (not saved.get("state") or not secrets.compare_digest(str(saved.get("state")).encode(), state.encode())
            or saved.get("provider") != provider or time.time() - started > oauth.STATE_SECONDS):
        return _oauth_back("This login link expired or didn't start here. Try again.", next_path)
    try:
        _limited(limits.LOGIN, request, "login attempts")
        who = await oauth.identity(provider, code, str(request.base_url))
        user = await oauth.account_for(provider, who, request.cookies.get("atelier_lang") or "")
        collab.note_user(user)
        await collab.claim_invites(user)           # projects someone invited this email to
        if user.get("created"):
            await notices.welcome(user)
        session = await auth.create_session(user["id"])
    except oauth.OAuthError as e:
        return _oauth_back(str(e), next_path)
    except auth.AuthError as e:
        return _oauth_back(str(e), next_path)
    except HTTPException as e:
        return _oauth_back(e.detail, next_path)
    except Exception as e:                         # the browser is mid-redirect: send it back to the login page, not to JSON
        ref = secrets.token_hex(3)
        status, detail, _ = explain_exception(e, ref)
        if status == 503:
            log.error("OAuth login with %s: database unavailable [ref %s]: %s", provider, ref, e.__class__.__name__)
        else:
            log.exception("OAuth login with %s failed [ref %s]", provider, ref)
        return _oauth_back(detail if status in (400, 503) else
                           f"Logging in with {oauth.PROVIDERS[provider]['name']} failed on our side (ref {ref}). Try again in a minute.",
                           next_path)
    res = RedirectResponse(next_path + ("&" if "?" in next_path else "?") + "welcome=back" if user.get("reactivated") else next_path,
                           status_code=303)
    res.set_cookie(auth.COOKIE, session, max_age=config.SESSION_DAYS * 86400, httponly=True,
                   samesite="lax", secure=config.COOKIE_SECURE, path="/")
    res.delete_cookie(oauth.STATE_COOKIE, path="/api/auth/oauth")
    return res


@router.get("/api/auth/identities")
async def my_identities(request: Request):
    return await oauth.identities(_confirmed_user(request)["id"])


@router.post("/api/auth/register")
async def register(request: Request, body: dict):
    if str(body.get("website") or "").strip():          # a field people never see: only bots fill it in
        raise HTTPException(400, "We couldn't create this account.")
    _limited(limits.SIGNUP, request, "new accounts")
    user = await auth.register(body)
    return _with_session(user, await auth.create_session(user["id"]))


@router.post("/api/auth/login")
async def login(request: Request, body: dict):
    _limited(limits.LOGIN, request, "login attempts")
    user = await auth.login(body.get("email"), body.get("password"))
    return _with_session(user, await auth.create_session(user["id"]))


@router.post("/api/auth/confirm")
async def confirm_email(request: Request, body: dict):
    if not request.state.user:
        raise HTTPException(401, "Log in first.")
    was = request.state.user["role"]
    user = await auth.confirm(request.state.user, body.get("code"))
    if user["role"] != "unconfirmed":
        collab.note_user(user)
        await collab.claim_invites(user)           # projects someone invited this email to before it had an account
        if was == "unconfirmed":
            await notices.welcome(user)
    return _with_session(user)


@router.post("/api/auth/resend")
async def resend_code(request: Request):
    user = request.state.user
    if not user:
        raise HTTPException(401, "Log in first.")
    if user["role"] != "unconfirmed":
        raise HTTPException(400, "Your email is already confirmed.")
    await auth.send_code(user)
    return {"ok": True}


@router.post("/api/auth/forgot")
async def forgot_password(request: Request, body: dict):
    _limited(limits.RESET, request, "password reset requests")
    await auth.request_reset(body.get("email"))
    return {"ok": True}          # the same answer whether or not the account exists


@router.post("/api/auth/reset")
async def reset_password(body: dict):
    await auth.reset_password(body.get("email"), body.get("code"), body.get("password"))
    res = JSONResponse({"ok": True})
    res.delete_cookie(auth.COOKIE, path="/")
    return res


def _confirmed_user(request: Request) -> dict:
    """/api/auth/ is public in _check_access, so the routes about your own account check here."""
    user = request.state.user
    if not user:
        raise HTTPException(401, "Log in first.")
    if user["role"] == "unconfirmed":
        raise HTTPException(403, "Confirm your email first.")
    return user


@router.patch("/api/auth/me")
async def update_me(request: Request, body: dict):
    return {"user": auth.public(await auth.update_profile(_confirmed_user(request), body))}


@router.patch("/api/auth/preferences")
async def update_preferences(request: Request, body: dict):
    return {"user": auth.public(await auth.set_preferences(_confirmed_user(request), body))}


@router.post("/api/auth/password")
async def change_password(request: Request, body: dict):
    await auth.change_password(_confirmed_user(request), body.get("current"), body.get("password"), request.cookies.get(auth.COOKIE))
    return {"ok": True}


@router.get("/api/auth/sessions")
async def other_sessions(request: Request):
    return {"others": await auth.count_other_sessions(_confirmed_user(request), request.cookies.get(auth.COOKIE))}


@router.delete("/api/auth/sessions")
async def end_other_sessions(request: Request):
    return {"ended": await auth.end_other_sessions(_confirmed_user(request), request.cookies.get(auth.COOKIE))}


@router.post("/api/auth/delete")
async def delete_account(request: Request, body: dict):
    """Deactivate your account: you're logged out everywhere, and it's deleted for good after ACCOUNT_DELETE_DAYS
    (your private agents and teams, your projects and their files) unless you log in again before then."""
    user = _confirmed_user(request)
    await auth.check_can_delete(user, body.get("password"))
    if user.get("billing_subscription"):          # a deactivated account isn't charged any more
        try:
            await billing.cancel(user, "Account deleted")
        except billing.BillingError:
            log.error("Couldn't cancel the PayPal subscription of %s on deletion", user["email"])
    owned = [m["id"] for m in store.all() if m.get("owner") == str(user["id"])]
    if any(is_busy(pid) for pid in owned):
        raise HTTPException(409, "Agents are working on one of your projects. Stop them first.")
    when = await auth.deactivate(user)
    res = JSONResponse({"ok": True, "delete_after": when, "days": config.ACCOUNT_DELETE_DAYS})
    res.delete_cookie(auth.COOKIE, path="/")
    return res


async def erase_account(user: dict):
    """The end of the 30 days: the account and everything it owns, for good."""
    owned = [m["id"] for m in store.all() if m.get("owner") == str(user["id"])]
    if any(is_busy(pid) for pid in owned):
        return                                   # can't happen without a session; try again next round
    await auth.delete_user(user)
    for pid in owned:
        _remove_project_files(pid)
    for people in collab.members.values():       # their memberships went with the account (ON DELETE CASCADE)
        people.pop(str(user["id"]), None)
    shutil.rmtree(BRAND_DIR / str(user["id"]), ignore_errors=True)
    shutil.rmtree(Path(config.STORAGE_DIR) / "claude-home" / str(user["id"]), ignore_errors=True)


async def purge_loop():
    """Every ACCOUNT_PURGE_SECONDS: delete the accounts whose time is up."""
    while True:
        try:
            for user in await auth.due_for_deletion():
                try:
                    await erase_account(user)
                except asyncio.CancelledError:
                    raise
                except Exception as e:           # one account that can't be erased doesn't keep the others
                    if db.unavailable(e):
                        raise
                    log.exception("Deleting the expired account %s failed; trying again next round", user.get("email"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if db.unavailable(e):
                log.error("Deleting expired accounts: the database isn't reachable (%s); trying again later", e.__class__.__name__)
            else:
                log.exception("Deleting expired accounts failed; trying again later")
        await asyncio.sleep(config.ACCOUNT_PURGE_SECONDS)


@router.get("/api/auth/export")
async def export_my_data(request: Request):
    """Everything stored about you, as a .zip: account, agents, teams, brand kit, and each project with its chat and files."""
    import tempfile
    user = _confirmed_user(request)
    row = await db.pool.fetchrow("SELECT * FROM users WHERE id = $1", user["id"])
    stamp = lambda v: v.isoformat() if v else None
    account = {**auth.public(row), "created_at": stamp(row["created_at"]), "confirmed_at": stamp(row["confirmed_at"]),
               "terms_accepted_at": stamp(row["terms_accepted_at"]), "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    buf = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", "Your data from Agents Atelier.\n\n"
                   "account.json   your account details and preferences (your password is stored only as a hash and isn't included)\n"
                   "agents/        the agents you made, in the same format as an agent export\n"
                   "teams/         the teams you made\n"
                   "brand/         your brand kit\n"
                   "ratings.json   the ratings you gave agents\n"
                   "projects/      each project: its details (project.json), its chat (chat.jsonl) and its files (site/)\n")
        z.writestr("account.json", json.dumps(account, indent=2, ensure_ascii=False))
        for a in await load_agents(user):
            if a.get("scope") == "mine":
                name, text = await team_files.export_agent(user, a["id"])
                z.writestr(f"agents/{name}", text)
        for t in await team_files.load_presets(user):
            if t.get("scope") == "mine":
                name, data = await team_files.export_team(user, t["id"])
                z.writestr(f"teams/{name}", json.dumps(data, indent=2, ensure_ascii=False))
        given = await db.pool.fetch("""SELECT a.name AS agent, r.project, r.value, r.comment, r.updated_at
                                        FROM agent_ratings r JOIN agents a ON a.pk = r.agent_pk WHERE r.user_id = $1""", user["id"])
        z.writestr("ratings.json", json.dumps([{**dict(r), "updated_at": stamp(r["updated_at"])} for r in given], indent=2, ensure_ascii=False))
        kit = await brand_kit(user)
        z.writestr("brand/brand.json", json.dumps({k: kit[k] for k in ("colors", "heading_font", "body_font")}, indent=2))
        if _brand_logo(user["id"]):
            z.write(_brand_logo(user["id"]), f"brand/{_brand_logo(user['id']).name}")
        for meta in sorted(store.all(), key=lambda m: m["created"]):
            if meta.get("owner") != str(user["id"]):
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", meta["name"].lower()).strip("-")[:40] or "project"
            root = f"projects/{slug}-{meta['id'][:8]}"
            z.writestr(f"{root}/project.json", json.dumps(meta, indent=2, ensure_ascii=False))
            chat = await store.events(meta["id"])
            if chat:
                z.writestr(f"{root}/chat.jsonl", "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in chat))
            for sub_name, folder in (("site", site_dir(meta["id"])), ("uploads", storage.uploads_dir(meta["id"]))):
                for f in sorted(folder.rglob("*")) if folder.exists() else []:
                    if f.is_file():
                        z.write(f, f"{root}/{sub_name}/{f.relative_to(folder)}")
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": 'attachment; filename="agents-atelier-my-data.zip"'})


@router.post("/api/auth/logout")
async def logout(request: Request):
    await auth.end_session(request.cookies.get(auth.COOKIE))
    res = JSONResponse({"ok": True})
    res.delete_cookie(auth.COOKIE, path="/")
    return res


@router.get("/api/auth/me")
async def me(request: Request):
    if not request.state.user:
        raise HTTPException(401, "Log in first.")
    return {"user": auth.public(request.state.user)}


# ---------------------------------------------------------------- plans and the member's own Claude key

@router.get("/api/plans")
async def list_plans(request: Request):
    """The plans, and for a logged-in member what they use of theirs."""
    out = {"plans": plans.public(), "keys": config.CLAUDE_KEYS, "payment": "paypal" if billing.enabled() else "request",
           "yearly": billing.yearly_enabled()}
    user = request.state.user
    if user:
        out["usage"] = {"projects": sum(m.get("owner") == str(user["id"]) for m in store.all())}
        out["subscription"] = bool(user.get("billing_subscription"))
    return out


@router.post("/api/billing/paypal/subscribe")
async def paypal_subscribe(request: Request, body: dict):
    """Start paying for a plan with PayPal: the answer's `approve` is the PayPal page to send the member to."""
    _limited(limits.CHECKOUTS, request, "payment attempts", per_member=True)
    return await billing.subscribe(_confirmed_user(request), str(body.get("plan") or ""), str(request.base_url),
                                   str(body.get("period") or "monthly"))


@router.get("/billing/paypal/return")
async def paypal_return(request: Request, subscription_id: str = ""):
    """PayPal sends the member back here after they approve (or leave) the payment."""
    user = _confirmed_user(request)
    try:
        state = await billing.confirm_return(user, subscription_id)
    except billing.BillingError:
        state = "error"
    await collab.refresh_user(user["id"])
    return RedirectResponse(f"/profile?paid={state}#plan", status_code=303)


@router.post("/api/billing/paypal/webhook")
async def paypal_webhook(request: Request):
    done = await billing.webhook(request.headers, await request.body())
    if done.get("user"):
        await collab.refresh_user(done["user"])
    return {"ok": True}


@router.post("/api/auth/plan")
async def change_my_plan(request: Request, body: dict):
    """Move to another plan: lower at once, higher as a request the admins approve."""
    me, plan = _confirmed_user(request), str(body.get("plan") or "")
    if billing.enabled() and me["role"] != "admin" and plan in plans.PLANS and plans.PLANS[plan]["price"]:
        raise HTTPException(400, "Paid plans are paid with PayPal: use the plan's button on your profile.")
    if plan == plans.DEFAULT and me.get("billing_subscription"):
        await billing.cancel(me)                  # moving to Starter: stop paying at PayPal first
    user = await auth.change_my_plan(me, plan, str(body.get("period") or "monthly"))
    await collab.refresh_user(me["id"])
    return {"user": user}


@router.delete("/api/auth/plan-request")
async def cancel_plan_request(request: Request):
    return {"user": await auth.cancel_plan_request(_confirmed_user(request))}


@router.put("/api/auth/claude-key")
async def put_claude_key(request: Request, body: dict):
    """Check the key with one tiny call on the member's own account, then keep it encrypted."""
    user = _confirmed_user(request)
    try:
        key = keys.clean(body.get("key"))
        await keys.check(key, str(user["id"]))
    except keys.KeyError_ as e:
        raise HTTPException(400, str(e))
    return {"user": await auth.save_claude_key(user, keys.encrypt(key), keys.hint(key))}


@router.delete("/api/auth/claude-key")
async def delete_claude_key(request: Request):
    return {"user": await auth.delete_claude_key(_confirmed_user(request))}


@router.put("/api/auth/run-limit")
async def put_run_limit(request: Request, body: dict):
    return {"user": await auth.set_run_limit(_confirmed_user(request), body.get("limit"))}
