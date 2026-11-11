"""Log in or sign up with Google or GitHub (OAuth 2.0 authorization code flow, server side, no extra packages).

  GET /api/auth/oauth/<provider>/start?next=/projects   → redirect to the provider (a random `state` in a short cookie)
  GET /api/auth/oauth/<provider>/callback?code&state    → back from the provider: check the state, exchange the code,
                                                            read the verified email, log in or create the account

Who the person is
  1. An account already linked to this provider's user id (user_identities): that account.
  2. Otherwise an account with the same email, which the provider says is verified: it gets linked (and confirmed, since
     the provider proved the address). A provider email that isn't verified is refused, so nobody can take over an
     account by adding someone else's address to their Google or GitHub profile.
  3. Otherwise a new account, confirmed at once, with no password (it can set one with "Forgot your password?" or on
     the profile). The buttons say that continuing accepts the Terms and the Privacy policy.

A provider is offered only when its client id and secret are set (GOOGLE_CLIENT_ID/SECRET, GITHUB_CLIENT_ID/SECRET).
Each provider's app must list the callback address <PUBLIC_URL>/api/auth/oauth/<provider>/callback.
"""

import asyncio
import base64
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import auth
import config
import db
import limits

log = logging.getLogger("agents-atelier.oauth")

STATE_COOKIE = "atelier_oauth"
STATE_SECONDS = 600

PROVIDERS = {
    "google": {
        "name": "Google",
        "client_id": config.GOOGLE_CLIENT_ID, "secret": config.GOOGLE_CLIENT_SECRET,
        "authorize": config.GOOGLE_AUTH_URL, "token": config.GOOGLE_TOKEN_URL,
        "scope": "openid email profile", "extra": {"prompt": "select_account"},
    },
    "github": {
        "name": "GitHub",
        "client_id": config.GITHUB_CLIENT_ID, "secret": config.GITHUB_CLIENT_SECRET,
        "authorize": config.GITHUB_AUTH_URL, "token": config.GITHUB_TOKEN_URL,
        "scope": "read:user user:email", "extra": {"allow_signup": "true"},
    },
}


class OAuthError(Exception):
    """Shown to the person on the login page (no detail from the provider)."""


def enabled() -> list[dict]:
    return [{"id": k, "name": p["name"]} for k, p in PROVIDERS.items() if p["client_id"] and p["secret"]]


def provider(pid: str) -> dict:
    p = PROVIDERS.get(pid)
    if not p or not p["client_id"] or not p["secret"]:
        raise OAuthError("This way of logging in isn't available.")
    return p


def callback_url(pid: str, base_url: str) -> str:
    return f"{config.PUBLIC_URL or base_url.rstrip('/')}/api/auth/oauth/{pid}/callback"


def safe_next(value: str | None) -> str:
    """Only a path on this site (never //evil.com or https://…)."""
    value = str(value or "")
    return value if value.startswith("/") and not value.startswith("//") and "\\" not in value and len(value) < 500 else "/"


def start(pid: str, base_url: str, next_path: str) -> tuple[str, str]:
    """(the provider's authorization URL, the value of the state cookie)."""
    p = provider(pid)
    state = secrets.token_urlsafe(24)
    params = {"client_id": p["client_id"], "redirect_uri": callback_url(pid, base_url), "response_type": "code",
              "scope": p["scope"], "state": state, **p["extra"]}
    cookie = base64.urlsafe_b64encode(json.dumps({"state": state, "provider": pid, "next": safe_next(next_path),
                                                  "at": time.time()}).encode()).decode()
    return f"{p['authorize']}?{urllib.parse.urlencode(params)}", cookie


def read_state(cookie: str | None) -> dict:
    """The state cookie set by start(), or {} when it's missing or damaged."""
    try:
        data = json.loads(base64.urlsafe_b64decode((cookie or "").encode()))
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _http(method: str, url: str, data: dict | None = None, token: str | None = None) -> dict | list:
    headers = {"Accept": "application/json", "User-Agent": "AgentsAtelier"}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"{}")


async def _call(*args, **kwargs):
    try:
        return await asyncio.to_thread(_http, *args, **kwargs)
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.warning("OAuth request failed: %s", e)
        raise OAuthError("We couldn't reach the provider. Try again in a moment.")


async def identity(pid: str, code: str, base_url: str) -> dict:
    """Exchange the code and read who this is: {subject, email, verified, name}."""
    p = provider(pid)
    tokens = await _call("POST", p["token"], {"client_id": p["client_id"], "client_secret": p["secret"], "code": code,
                                               "redirect_uri": callback_url(pid, base_url), "grant_type": "authorization_code"})
    token = tokens.get("access_token") if isinstance(tokens, dict) else None
    if not token:
        raise OAuthError("The provider didn't confirm the login. Try again.")
    if pid == "google":
        info = await _call("GET", config.GOOGLE_USERINFO_URL, token=token)
        info = info if isinstance(info, dict) else {}         # an odd answer: account_for says the provider didn't say who
        return {"subject": str(info.get("sub") or ""), "email": str(info.get("email") or "").lower(),
                "verified": info.get("email_verified") is True, "name": str(info.get("name") or "")}
    user = await _call("GET", f"{config.GITHUB_API_URL}/user", token=token)
    user = user if isinstance(user, dict) else {}
    emails = await _call("GET", f"{config.GITHUB_API_URL}/user/emails", token=token)
    emails = [e for e in emails if isinstance(e, dict)] if isinstance(emails, list) else []
    best = next((e for e in emails if e.get("primary") and e.get("verified")), None)
    best = best or next((e for e in emails if e.get("verified")), None)
    return {"subject": str(user.get("id") or ""), "email": str((best or {}).get("email") or "").lower(),
            "verified": bool(best), "name": str(user.get("name") or user.get("login") or "")}


async def account_for(pid: str, who: dict, language: str = "") -> dict:
    """The account to log in: linked, matched by verified email, or new."""
    if not who["subject"]:
        raise OAuthError("The provider didn't say who you are. Try again.")
    row = await db.pool.fetchrow("""SELECT u.* FROM user_identities i JOIN users u ON u.id = i.user_id
                                    WHERE i.provider = $1 AND i.subject = $2""", pid, who["subject"])
    if not row:
        if not who["email"] or not who["verified"]:
            raise OAuthError(f"Your {PROVIDERS[pid]['name']} account has no verified email address. Verify one there, "
                             "or sign up with your email and a password.")
        email = auth.clean_email(who["email"])
        row = await db.pool.fetchrow("SELECT * FROM users WHERE email = $1", email)
        if not row:
            if limits.is_disposable(email):
                raise OAuthError("Use a permanent email address. Temporary inboxes can't receive your account emails for long.")
            role = "admin" if config.ADMIN_EMAIL and email == config.ADMIN_EMAIL else "user"
            row = await db.pool.fetchrow(
                """INSERT INTO users (id, email, password_hash, name, role, language, terms_accepted_at, confirmed_at)
                   VALUES ($1, $2, '', $3, $4, $5, now(), now()) RETURNING *""",
                uuid.uuid4(), email, " ".join(who["name"].split())[:80], role, language if language in auth.LANGUAGES else "")
            log.info("New account %s through %s", email, pid)
        await db.pool.execute("""INSERT INTO user_identities (provider, subject, user_id, email, created_at) VALUES ($1, $2, $3, $4, $5)
                                 ON CONFLICT (provider, subject) DO NOTHING""", pid, who["subject"], row["id"], email, time.time())
    if row["role"] == "unconfirmed":           # the provider verified this address
        role = "admin" if config.ADMIN_EMAIL and row["email"] == config.ADMIN_EMAIL else "user"
        row = await db.pool.fetchrow("UPDATE users SET role = $2, confirmed_at = now() WHERE id = $1 RETURNING *", row["id"], role)
    back = bool(row["deactivated_at"])         # logging in during the 30 days cancels the deletion, as with a password
    row = await db.pool.fetchrow("""UPDATE users SET deactivated_at = NULL, delete_after = NULL, failed_logins = 0, locked_until = NULL
                                    WHERE id = $1 RETURNING *""", row["id"])
    return {**dict(row), "reactivated": back}


async def identities(user_id) -> list[dict]:
    rows = await db.pool.fetch("SELECT provider, email, created_at FROM user_identities WHERE user_id = $1 ORDER BY created_at", user_id)
    return [{"provider": r["provider"], "name": PROVIDERS.get(r["provider"], {}).get("name", r["provider"]), "email": r["email"],
             "since": r["created_at"]} for r in rows]
