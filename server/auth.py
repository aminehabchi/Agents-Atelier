"""Accounts: register, log in, confirm the email with a 6-digit code, sessions and roles.

Roles
  unconfirmed  just registered: can only confirm its email (or log out)
  user         confirmed: builds projects, sees the shared agents and teams, owns its own
  admin        a user who can also change the shared agents, teams and rules, and manage users

Passwords are hashed with scrypt (Python's standard library). Session cookies hold a random token; only its SHA-256
is stored. Confirmation codes are stored hashed too, expire after CODE_MINUTES and allow MAX_CODE_TRIES tries.
"""

import asyncio
import base64
import datetime as dt
import hashlib
import hmac
import json
import logging
import re
import secrets
import smtplib
import ssl
import time
import uuid
from email.message import EmailMessage

import config
import db
import limits
import plans

log = logging.getLogger("agents-atelier.auth")

COOKIE = "atelier_session"
ROLES = ("admin", "user", "unconfirmed")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
MIN_PASSWORD = 8
MAX_PASSWORD = 200
MAX_CODE_TRIES = 5
RESEND_SECONDS = 60
MAX_FAILED_LOGINS = 10
LOCK_MINUTES = 15
# A password reset can be asked for by anyone who knows an email, so its 6 digits need limits per day as well as per code.
MAX_RESET_SENDS = 5
MAX_RESET_FAILS = 15
RESET_WINDOW = dt.timedelta(hours=24)
LANGUAGES = ("en", "fr", "ary")      # ary: Moroccan Darija
THEMES = ("system", "light", "dark")
MIN_NAME = 2
MAX_NAME = 80
MAX_COMPANY = 80
# The questions with fixed answers on the sign-up form (all optional). The form reads them from /api/auth/options.
PROFILE_OPTIONS = {
    "job_role": ["Developer", "Designer", "Founder or owner", "Marketing", "Freelancer", "Student", "Other"],
    "use_case": ["My business", "Client work", "A personal project", "Learning or trying it out"],
    "referral": ["Search engine", "Social media", "A friend or colleague", "YouTube", "Other"],
}


class AuthError(Exception):
    """A readable message for the user, with the HTTP status to answer with."""
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(key).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, key = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(base64.b64decode(key)))
        return hmac.compare_digest(got, base64.b64decode(key))
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password(secrets.token_hex(8))   # compared against when the email is unknown (same timing)


def clean_email(value) -> str:
    email = str(value or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address.")
    return email


def check_password(value) -> str:
    password = str(value or "")
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"Use at least {MIN_PASSWORD} characters for your password.")
    if len(password) > MAX_PASSWORD:
        raise AuthError(f"Keep your password under {MAX_PASSWORD} characters.")
    return password


def _one_line(value) -> str:
    """Printable text on one line: no control characters, single spaces."""
    return " ".join("".join(c if c.isprintable() else " " for c in str(value or "")).split())


def clean_profile(body: dict) -> dict:
    """The sign-up answers, checked: a name is required, the rest is optional."""
    name, company = _one_line(body.get("name")), _one_line(body.get("company"))
    if not MIN_NAME <= len(name) <= MAX_NAME:
        raise AuthError(f"Enter your full name ({MIN_NAME} to {MAX_NAME} characters).")
    if len(company) > MAX_COMPANY:
        raise AuthError(f"Keep the company name under {MAX_COMPANY} characters.")
    profile = {"name": name, "company": company}
    for field, allowed in PROFILE_OPTIONS.items():
        answer = str(body.get(field) or "")
        if answer and answer not in allowed:
            raise AuthError("Pick one of the answers in the list.")
        profile[field] = answer
    return profile


def public(row) -> dict:
    """What the browser may know about a user."""
    return {"id": str(row["id"]), "email": row["email"], "role": row["role"],
            "confirmed": row["role"] != "unconfirmed",
            "name": row["name"], "company": row["company"], "job_role": row["job_role"],
            "use_case": row["use_case"], "referral": row["referral"], "language": row["language"], "theme": row["theme"],
            "created": row["created_at"].timestamp() if row.get("created_at") else None,
            # the plan and the Claude key's state; the key itself (even encrypted) never leaves the server
            "plan": row.get("plan") or plans.DEFAULT,
            "claude_key": {"set": bool(row.get("claude_key")), "hint": row.get("claude_key_hint") or "",
                           "checked": row.get("claude_key_at")},
            "run_limit": plans.run_limit(dict(row)),
            "plan_request": row.get("plan_request") or "", "plan_request_at": row.get("plan_request_at"),
            "plan_request_period": row.get("plan_request_period") or "monthly",
            "paid_online": bool(row.get("billing_subscription")),   # the plan is a PayPal subscription (see billing.py)
            "billing_period": row.get("billing_period") or "monthly",  # monthly · yearly
            "has_password": bool(row.get("password_hash")),
            "notify_runs": row.get("notify_runs", True) is not False,   # email when a long run finishes (notify.py)         # accounts made with Google or GitHub start without one
            "deactivated_at": row.get("deactivated_at"), "delete_after": row.get("delete_after"),
            **({"reactivated": True} if row.get("reactivated") else {})}   # just logged in during the 30 days


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------- register · login · sessions

async def register(body: dict) -> dict:
    email, password = clean_email(body.get("email")), check_password(body.get("password"))
    p = clean_profile(body)
    if body.get("accept_terms") is not True:
        raise AuthError("Please accept the Terms and the Privacy policy to create an account.")
    if limits.is_disposable(email):
        raise AuthError("Use a permanent email address. Temporary inboxes can't receive your account emails for long.")
    try:
        row = await db.pool.fetchrow(
            """INSERT INTO users (id, email, password_hash, name, company, job_role, use_case, referral, language, terms_accepted_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now()) RETURNING *""",
            uuid.uuid4(), email, hash_password(password),
            p["name"], p["company"], p["job_role"], p["use_case"], p["referral"],
            body.get("language") if body.get("language") in LANGUAGES else "")
    except Exception as e:
        if e.__class__.__name__ == "UniqueViolationError":
            raise AuthError("An account with this email already exists. Log in instead.", 409)
        raise
    await send_code(row)
    return dict(row)


def _check_lock(row):
    if row["locked_until"] and row["locked_until"] > now():
        minutes = max(1, int((row["locked_until"] - now()).total_seconds() // 60) + 1)
        raise AuthError(f"Too many failed attempts. Try again in {minutes} minute{'s' if minutes > 1 else ''}.", 429)


async def _note_failed_password(row):
    fails = row["failed_logins"] + 1
    lock = now() + dt.timedelta(minutes=LOCK_MINUTES) if fails >= MAX_FAILED_LOGINS else None
    await db.pool.execute("UPDATE users SET failed_logins = $2, locked_until = $3 WHERE id = $1",
                          row["id"], 0 if lock else fails, lock)


async def login(email, password) -> dict:
    email = str(email or "").strip().lower()
    row = await db.pool.fetchrow("SELECT * FROM users WHERE email = $1", email)
    if row:
        _check_lock(row)
    if not verify_password(str(password or ""), row["password_hash"] if row else _DUMMY_HASH) or not row:
        if row:
            await _note_failed_password(row)
        raise AuthError("Wrong email or password.", 401)
    back = bool(row["deactivated_at"])          # logging in during the 30 days cancels the deletion
    row = await db.pool.fetchrow("UPDATE users SET failed_logins = 0, locked_until = NULL, deactivated_at = NULL, delete_after = NULL "
                                 "WHERE id = $1 RETURNING *", row["id"])
    if back:
        log.info("%s logged in again: their account deletion is cancelled", row["email"])
    if row["role"] == "unconfirmed":
        await send_code(row, quiet=True)          # a fresh code, unless one was sent less than a minute ago
    elif config.ADMIN_EMAIL and row["email"] == config.ADMIN_EMAIL and row["role"] != "admin":
        row = await db.pool.fetchrow("UPDATE users SET role = 'admin' WHERE id = $1 RETURNING *", row["id"])
    return {**dict(row), "reactivated": back}


async def create_session(user_id) -> str:
    token = secrets.token_urlsafe(32)
    await db.pool.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES ($1, $2, $3)",
                          _hash(token), user_id, now() + dt.timedelta(days=config.SESSION_DAYS))
    return token


async def end_session(token: str | None):
    if token:
        await db.pool.execute("DELETE FROM sessions WHERE token_hash = $1", _hash(token))


async def user_for_token(token: str | None) -> dict | None:
    if not token or len(token) > 200:
        return None
    row = await db.pool.fetchrow(
        """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = $1 AND s.expires_at > now()""", _hash(token))
    return dict(row) if row else None


# ---------------------------------------------------------------- email confirmation

EMAILS = {
    "confirm": {
        "en": ("{code} is your Agents Atelier code",
               "Hello,\n\nYour confirmation code is: {code}\n\n"
               "Enter it in Agents Atelier to confirm your email. It works for {minutes} minutes.\n\n"
               "If you didn't create an account, you can ignore this email.\n"),
        "fr": ("{code} est votre code Agents Atelier",
               "Bonjour,\n\nVotre code de confirmation : {code}\n\n"
               "Saisissez-le dans Agents Atelier pour confirmer votre e-mail. Il est valable {minutes} minutes.\n\n"
               "Si vous n'avez pas créé de compte, ignorez cet e-mail.\n"),
        "ary": ("{code} هو الكود ديالك ف Agents Atelier",
                "السلام،\n\nالكود ديال التأكيد ديالك هو: {code}\n\n"
                "دخلو ف Agents Atelier باش تأكد الإيميل ديالك. خدام لمدة {minutes} دقيقة.\n\n"
                "إلا ما درتيش حساب، ما تدير والو ف هاد الإيميل.\n"),
    },
    "reset": {
        "en": ("{code} is your password reset code",
               "Hello,\n\nYour password reset code is: {code}\n\n"
               "Enter it in Agents Atelier to choose a new password. It works for {minutes} minutes.\n\n"
               "If you didn't ask for this, you can ignore this email. Your password hasn't changed.\n"),
        "fr": ("{code} est votre code de réinitialisation",
               "Bonjour,\n\nVotre code de réinitialisation du mot de passe : {code}\n\n"
               "Saisissez-le dans Agents Atelier pour choisir un nouveau mot de passe. Il est valable {minutes} minutes.\n\n"
               "Si vous n'êtes pas à l'origine de cette demande, ignorez cet e-mail. Votre mot de passe n'a pas changé.\n"),
        "ary": ("{code} هو الكود باش تبدل كلمة السر",
                "السلام،\n\nالكود باش تبدل كلمة السر ديالك هو: {code}\n\n"
                "دخلو ف Agents Atelier باش تختار كلمة سر جديدة. خدام لمدة {minutes} دقيقة.\n\n"
                "إلا ما طلبتيش هادشي، ما تدير والو. كلمة السر ديالك ما تبدلاتش.\n"),
    },
    "changed": {
        "en": ("Your Agents Atelier password was changed",
               "Hello,\n\nThe password of your Agents Atelier account was just changed.\n\n"
               "If this wasn't you, use \"Forgot your password?\" on the login page right away.\n"),
        "fr": ("Votre mot de passe Agents Atelier a été modifié",
               "Bonjour,\n\nLe mot de passe de votre compte Agents Atelier vient d'être modifié.\n\n"
               "Si ce n'est pas vous, utilisez tout de suite « Mot de passe oublié ? » sur la page de connexion.\n"),
        "ary": ("كلمة السر ديال حسابك ف Agents Atelier تبدلات",
                "السلام،\n\nكلمة السر ديال حسابك ف Agents Atelier يالاه تبدلات.\n\n"
                "إلا ماشي نتا اللي بدلتيها، دخل دابا لصفحة الدخول وكليكي على \"نسيتي كلمة السر؟\".\n"),
    },
    "invited": {
        "en": ("You were added to a project on Agents Atelier",
               "Hello,\n\nSomeone added you to one of their projects on Agents Atelier.\n\n"
               "Log in and open Projects: it's listed under \"Shared with me\".\n"),
        "fr": ("Vous avez été ajouté à un projet sur Agents Atelier",
               "Bonjour,\n\nQuelqu'un vous a ajouté à l'un de ses projets sur Agents Atelier.\n\n"
               "Connectez-vous et ouvrez Projets : il apparaît dans « Partagés avec moi ».\n"),
        "ary": ("تزاديتي لواحد المشروع ف Agents Atelier",
                "السلام،\n\nشي واحد زادك لواحد من المشاريع ديالو ف Agents Atelier.\n\n"
                "دخل لحسابك وحل المشاريع: غادي تلقاه ف \"مشاركين معايا\".\n"),
    },
    "invited_signup": {
        "en": ("You're invited to a project on Agents Atelier",
               "Hello,\n\nSomeone invited you to work with them on a project on Agents Atelier.\n\n"
               "Create an account with this email address and confirm it: the project will be waiting under "
               "\"Shared with me\" in Projects.\n\nIf you don't know who sent this, you can ignore this email.\n"),
        "fr": ("Vous êtes invité à un projet sur Agents Atelier",
               "Bonjour,\n\nQuelqu'un vous invite à travailler avec lui sur un projet sur Agents Atelier.\n\n"
               "Créez un compte avec cette adresse e-mail et confirmez-le : le projet vous attendra dans "
               "« Partagés avec moi », dans Projets.\n\nSi vous ne savez pas qui l'a envoyé, ignorez cet e-mail.\n"),
        "ary": ("عندك دعوة لواحد المشروع ف Agents Atelier",
                "السلام،\n\nشي واحد عرض عليك تخدم معاه ف واحد المشروع ف Agents Atelier.\n\n"
                "دير حساب بهاد الإيميل وأكدو: المشروع غادي يكون كيتسناك ف \"مشاركين معايا\" ف المشاريع.\n\n"
                "إلا ما عرفتيش شكون صيفط هادشي، ما تدير والو.\n"),
    },
}


def _email(kind: str, language: str, **values) -> tuple[str, str]:
    """(subject, text) in the account's language. Nothing a person typed ever goes in an email."""
    subject, text = EMAILS[kind][language if language in EMAILS[kind] else "en"]
    return subject.format(**values), text.format(**values)


async def send_code(user, quiet: bool = False):
    """Email a new 6-digit code. Within RESEND_SECONDS of the last one: an error, or nothing when quiet."""
    last = await db.pool.fetchval("SELECT sent_at FROM email_codes WHERE user_id = $1", user["id"])
    if last and (now() - last).total_seconds() < RESEND_SECONDS:
        if quiet:
            return
        wait = RESEND_SECONDS - int((now() - last).total_seconds())
        raise AuthError(f"A code was just sent. You can ask for a new one in {wait} seconds.", 429)
    code = f"{secrets.randbelow(10 ** 6):06d}"
    await db.pool.execute(
        """INSERT INTO email_codes (user_id, code_hash, expires_at, attempts, sent_at) VALUES ($1, $2, $3, 0, now())
           ON CONFLICT (user_id) DO UPDATE SET code_hash = $2, expires_at = $3, attempts = 0, sent_at = now()""",
        user["id"], _hash(f"{user['id']}:{code}"), now() + dt.timedelta(minutes=config.CODE_MINUTES))
    subject, text = _email("confirm", user["language"], code=code, minutes=config.CODE_MINUTES)
    await send_email(user["email"], subject, text, code=code)


async def confirm(user, code) -> dict:
    if user["role"] != "unconfirmed":
        return user
    code = re.sub(r"\D", "", str(code or ""))
    row = await db.pool.fetchrow("SELECT * FROM email_codes WHERE user_id = $1", user["id"])
    if not row or row["expires_at"] < now():
        raise AuthError("This code has expired. Ask for a new one.", 410)
    if row["attempts"] >= MAX_CODE_TRIES:
        raise AuthError("Too many wrong codes. Ask for a new one.", 429)
    if len(code) != 6 or not hmac.compare_digest(_hash(f"{user['id']}:{code}"), row["code_hash"]):
        await db.pool.execute("UPDATE email_codes SET attempts = attempts + 1 WHERE user_id = $1", user["id"])
        left = MAX_CODE_TRIES - row["attempts"] - 1
        raise AuthError(f"That code isn't right. {left} tr{'y' if left == 1 else 'ies'} left." if left
                        else "That code isn't right. Ask for a new one.", 400)
    role = "admin" if config.ADMIN_EMAIL and user["email"] == config.ADMIN_EMAIL else "user"
    async with db.pool.acquire() as con:
        async with con.transaction():
            await con.execute("DELETE FROM email_codes WHERE user_id = $1", user["id"])
            row = await con.fetchrow("UPDATE users SET role = $2, confirmed_at = now() WHERE id = $1 RETURNING *", user["id"], role)
    log.info("Confirmed %s as %s", row["email"], role)
    return dict(row)


async def send_email(to: str, subject: str, text: str, code: str | None = None):
    """Send by SMTP when SMTP_HOST is set; otherwise write the code to the server log. EMAIL_OUTBOX keeps a copy."""
    if config.EMAIL_OUTBOX:
        config.EMAIL_OUTBOX.parent.mkdir(parents=True, exist_ok=True)
        with open(config.EMAIL_OUTBOX, "a") as f:
            f.write(json.dumps({"to": to, "subject": subject, "text": text, "code": code, "ts": time.time()}) + "\n")
    if not config.SMTP_HOST:
        log.warning("Email not sent (SMTP_HOST isn't set). To %s: %s", to, subject)
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = config.SMTP_FROM, to, subject
    msg.set_content(text)
    try:
        await asyncio.to_thread(_smtp_send, msg)
    except (OSError, smtplib.SMTPException, ValueError) as e:     # ValueError: an address SMTP can't encode
        log.error("Couldn't send email to %s: %s", to, e)
        raise AuthError("We couldn't send the email. Try again in a moment, or contact the administrator.", 502)


def _smtp_send(msg: EmailMessage):
    context = ssl.create_default_context()
    if config.SMTP_SECURITY == "ssl":
        server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=context, timeout=20)
    else:
        server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
    with server:
        if config.SMTP_SECURITY == "starttls":
            server.starttls(context=context)
        if config.SMTP_USER:
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.send_message(msg)


# ---------------------------------------------------------------- forgotten passwords

_background: set[asyncio.Task] = set()


def _send_later(to: str, subject: str, text: str, code: str | None = None):
    """Send without waiting: how long an answer takes, or a mail failure, must not show whether an account exists."""
    async def send():
        try:
            await send_email(to, subject, text, code=code)
        except AuthError:
            pass        # send_email has logged it
        except Exception:
            log.exception("Couldn't send the email “%s” to %s", subject, to)
    task = asyncio.create_task(send())
    _background.add(task)
    task.add_done_callback(_background.discard)


send_later = _send_later       # for the other modules (collab.py): an email that never blocks or fails the request


def email_text(kind: str, language: str, **values) -> tuple[str, str]:
    return _email(kind, language, **values)


def _password_changed_email(user):
    _send_later(user["email"], *_email("changed", user["language"]))


async def request_reset(email):
    """Email a reset code if the account exists. The answer is the same either way, and limits apply silently."""
    email = clean_email(email)
    user = await db.pool.fetchrow("SELECT id, email, language FROM users WHERE email = $1", email)
    if not user:
        return
    row = await db.pool.fetchrow("SELECT * FROM reset_codes WHERE user_id = $1", user["id"])
    fresh_window = not row or now() - row["window_start"] > RESET_WINDOW
    if not fresh_window and ((now() - row["sent_at"]).total_seconds() < RESEND_SECONDS or row["sends"] >= MAX_RESET_SENDS):
        return
    code = f"{secrets.randbelow(10 ** 6):06d}"
    await db.pool.execute(
        """INSERT INTO reset_codes (user_id, code_hash, expires_at, attempts, sent_at, window_start, sends, fails)
           VALUES ($1, $2, $3, 0, now(), now(), 1, 0)
           ON CONFLICT (user_id) DO UPDATE SET code_hash = $2, expires_at = $3, attempts = 0, sent_at = now(),
             window_start = CASE WHEN $4 THEN now() ELSE reset_codes.window_start END,
             sends = CASE WHEN $4 THEN 1 ELSE reset_codes.sends + 1 END,
             fails = CASE WHEN $4 THEN 0 ELSE reset_codes.fails END""",
        user["id"], _hash(f"reset:{user['id']}:{code}"), now() + dt.timedelta(minutes=config.CODE_MINUTES), fresh_window)
    _send_later(email, *_email("reset", user["language"], code=code, minutes=config.CODE_MINUTES), code=code)


async def reset_password(email, code, password):
    """Set a new password with an emailed code. This also proves the address, and ends every session."""
    password = check_password(password)
    bad = AuthError("That code isn't right or has expired. Check it, or ask for a new one.")
    email, code = str(email or "").strip().lower(), re.sub(r"\D", "", str(code or ""))
    user = await db.pool.fetchrow("SELECT * FROM users WHERE email = $1", email)
    row = user and await db.pool.fetchrow("SELECT * FROM reset_codes WHERE user_id = $1", user["id"])
    if not row or not row["code_hash"] or row["expires_at"] < now() or row["attempts"] >= MAX_CODE_TRIES or row["fails"] >= MAX_RESET_FAILS:
        raise bad
    if len(code) != 6 or not hmac.compare_digest(_hash(f"reset:{user['id']}:{code}"), row["code_hash"]):
        await db.pool.execute("UPDATE reset_codes SET attempts = attempts + 1, fails = fails + 1 WHERE user_id = $1", user["id"])
        raise bad
    role = user["role"]
    if role == "unconfirmed":
        role = "admin" if config.ADMIN_EMAIL and email == config.ADMIN_EMAIL else "user"
    async with db.pool.acquire() as con:
        async with con.transaction():
            await con.execute(
                """UPDATE users SET password_hash = $2, role = $3, confirmed_at = coalesce(confirmed_at, now()),
                   failed_logins = 0, locked_until = NULL WHERE id = $1""", user["id"], hash_password(password), role)
            await con.execute("UPDATE reset_codes SET code_hash = NULL WHERE user_id = $1", user["id"])
            await con.execute("DELETE FROM email_codes WHERE user_id = $1", user["id"])
            await con.execute("DELETE FROM sessions WHERE user_id = $1", user["id"])
    _password_changed_email(user)


# ---------------------------------------------------------------- your own account

async def update_profile(user: dict, body: dict) -> dict:
    p = clean_profile(body)
    row = await db.pool.fetchrow(
        "UPDATE users SET name = $2, company = $3, job_role = $4, use_case = $5, referral = $6 WHERE id = $1 RETURNING *",
        user["id"], p["name"], p["company"], p["job_role"], p["use_case"], p["referral"])
    return dict(row)


async def set_preferences(user: dict, body: dict) -> dict:
    language, theme = body.get("language", user["language"]), body.get("theme", user["theme"])
    if language not in LANGUAGES + ("",) or theme not in THEMES + ("",):
        raise AuthError("That language or theme isn't available.")
    notify_runs = body.get("notify_runs", user.get("notify_runs", True))
    if not isinstance(notify_runs, bool):
        raise AuthError("“notify_runs” must be true or false.")
    row = await db.pool.fetchrow("UPDATE users SET language = $2, theme = $3, notify_runs = $4 WHERE id = $1 RETURNING *",
                                 user["id"], language, theme, notify_runs)
    return dict(row)


async def _check_current_password(user: dict, password):
    """Someone may have found an unlocked screen, so these guesses count towards the same lock as logins."""
    _check_lock(user)
    if not user["password_hash"]:                 # an account made with Google or GitHub: the session is the proof
        return
    if not verify_password(str(password or ""), user["password_hash"]):
        await _note_failed_password(user)
        raise AuthError("Your current password isn't right.")


async def change_password(user: dict, current, new, keep_token: str | None):
    await _check_current_password(user, current)
    new = check_password(new)
    if new == current and user["password_hash"]:
        raise AuthError("Choose a password different from your current one.")
    await db.pool.execute("UPDATE users SET password_hash = $2, failed_logins = 0 WHERE id = $1", user["id"], hash_password(new))
    await end_other_sessions(user, keep_token)
    _password_changed_email(user)


async def count_other_sessions(user: dict, token: str | None) -> int:
    return await db.pool.fetchval("SELECT count(*) FROM sessions WHERE user_id = $1 AND expires_at > now() AND token_hash <> $2",
                                  user["id"], _hash(token or ""))


async def end_other_sessions(user: dict, token: str | None) -> int:
    done = await db.pool.execute("DELETE FROM sessions WHERE user_id = $1 AND token_hash <> $2", user["id"], _hash(token or ""))
    return int(done.split()[-1])


async def check_can_delete(user: dict, password):
    await _check_current_password(user, password)
    if user["role"] == "admin" and await db.pool.fetchval("SELECT count(*) FROM users WHERE role = 'admin'") <= 1:
        raise AuthError("You're the only admin. Make someone else an admin before deleting your account.")


async def deactivate(user: dict) -> float:
    """Step one of deleting an account: logged out everywhere, then removed for good after ACCOUNT_DELETE_DAYS
    unless they log in again. Returns when it will be deleted."""
    now = time.time()
    when = now + config.ACCOUNT_DELETE_DAYS * 86400
    async with db.pool.acquire() as con, con.transaction():
        await con.execute("UPDATE users SET deactivated_at = $2, delete_after = $3 WHERE id = $1", user["id"], now, when)
        await con.execute("DELETE FROM sessions WHERE user_id = $1", user["id"])
    day = dt.datetime.fromtimestamp(when, dt.timezone.utc).strftime("%-d %B %Y")
    _send_later(user["email"], "Your Agents Atelier account will be deleted",
                f"Hello,\n\nYour Agents Atelier account is deactivated. It will be deleted for good on {day}, "
                "with your projects, agents and teams.\n\nChanged your mind? Log in before then and everything comes back "
                "as it was.\n")
    log.info("Deactivated account %s (deleted after %s)", user["email"], day)
    return when


async def due_for_deletion() -> list[dict]:
    """Deactivated accounts whose 30 days are over."""
    rows = await db.pool.fetch("SELECT * FROM users WHERE delete_after IS NOT NULL AND delete_after <= $1", time.time())
    return [dict(r) for r in rows]


async def delete_user(user: dict):
    """Removes the account; its sessions, private agents and teams go with it (ON DELETE CASCADE)."""
    await db.pool.execute("DELETE FROM users WHERE id = $1", user["id"])
    log.info("Deleted account %s", user["email"])


# ---------------------------------------------------------------- admin: users

async def list_users() -> list[dict]:
    rows = await db.pool.fetch("SELECT * FROM users ORDER BY created_at")
    return [public(r) for r in rows]


async def set_role(actor: dict, user_id: str, role: str) -> dict:
    if role not in ROLES:
        raise AuthError("A role is admin, user or unconfirmed.")
    try:
        uid = uuid.UUID(str(user_id))
    except ValueError:
        raise AuthError("Unknown user.", 404)
    if uid == actor["id"]:
        raise AuthError("You can't change your own role. Ask another admin.", 400)
    row = await db.pool.fetchrow("UPDATE users SET role = $2, confirmed_at = coalesce(confirmed_at, CASE WHEN $2 <> 'unconfirmed' THEN now() END) "
                                 "WHERE id = $1 RETURNING *", uid, role)
    if not row:
        raise AuthError("Unknown user.", 404)
    return public(row)


async def set_plan(actor: dict, user_id: str, plan: str) -> dict:
    if plan not in plans.PLANS:
        raise AuthError("A plan is starter, pro or agency.")
    try:
        uid = uuid.UUID(str(user_id))
    except ValueError:
        raise AuthError("Unknown user.", 404)
    before = await db.pool.fetchval("SELECT plan FROM users WHERE id = $1", uid)
    # approving a request: the member pays the way they asked (monthly or yearly); any other change is monthly
    row = await db.pool.fetchrow("""UPDATE users SET plan = $2,
                                      billing_period = CASE WHEN plan_request = $2 THEN plan_request_period ELSE 'monthly' END,
                                      plan_request = '', plan_request_at = NULL WHERE id = $1 RETURNING *""", uid, plan)
    if not row:
        raise AuthError("Unknown user.", 404)
    await _plan_event(uid, before, plan, by_admin=True)
    log.info("%s set the plan of %s to %s", actor["email"], row["email"], plan)
    return public(row)


async def _plan_event(user_id, before: str | None, after: str, by_admin: bool):
    if before and before != after:
        await db.pool.execute("INSERT INTO plan_events (user_id, from_plan, to_plan, by_admin, ts) VALUES ($1, $2, $3, $4, $5)",
                              user_id, before, after, by_admin, time.time())


async def decline_plan_request(actor: dict, user_id: str) -> dict:
    try:
        uid = uuid.UUID(str(user_id))
    except ValueError:
        raise AuthError("Unknown user.", 404)
    row = await db.pool.fetchrow("UPDATE users SET plan_request = '', plan_request_at = NULL WHERE id = $1 RETURNING *", uid)
    if not row:
        raise AuthError("Unknown user.", 404)
    log.info("%s declined the plan request of %s", actor["email"], row["email"])
    return public(row)


async def change_my_plan(user: dict, plan: str, period: str = "monthly") -> dict:
    """A member's own choice: a lower plan applies at once; a higher one is a request an admin approves
    (no online payment yet). The admins get an email about it."""
    if plan not in plans.PLANS:
        raise AuthError("Pick Starter, Pro or Agency.")
    if user["role"] == "admin":
        raise AuthError("Admins already have every feature.")
    if period not in plans.PERIODS:
        raise AuthError("Pick monthly or yearly.")
    current = user.get("plan") or plans.DEFAULT
    if plan == current:
        raise AuthError(f"You're already on {plans.PLANS[plan]['name']}.")
    if plans.rank(plan) < plans.rank(current):
        row = await db.pool.fetchrow("UPDATE users SET plan = $2, plan_request = '', plan_request_at = NULL WHERE id = $1 RETURNING *",
                                     user["id"], plan)
        await _plan_event(user["id"], current, plan, by_admin=False)
        log.info("%s moved down to %s", user["email"], plan)
        return public(row)
    row = await db.pool.fetchrow("UPDATE users SET plan_request = $2, plan_request_at = $3, plan_request_period = $4 WHERE id = $1 RETURNING *",
                                 user["id"], plan, time.time(), period)
    log.info("%s asked for the %s plan (%s)", user["email"], plan, period)
    admins = await db.pool.fetch("SELECT email FROM users WHERE role = 'admin'")
    p = plans.PLANS[plan]
    name = p["name"]
    how = (f"paid yearly: ${p['price_yearly']} a year, two months free" if period == "yearly" and p.get("price_yearly")
           else f"paid monthly: ${p['price']} a month")
    for a in admins:
        _send_later(a["email"], f"{user['email']} asked for the {name} plan",
                    f"Hello,\n\n{user.get('name') or user['email']} ({user['email']}) asked to move from "
                    f"{plans.PLANS[current]['name']} to {name}, {how}.\n\nOpen the Users page in Agents Atelier to approve or decline it.\n")
    return public(row)


async def cancel_plan_request(user: dict) -> dict:
    row = await db.pool.fetchrow("UPDATE users SET plan_request = '', plan_request_at = NULL WHERE id = $1 RETURNING *", user["id"])
    return public(row)


# ---------------------------------------------------------------- the member's own Claude API key

async def save_claude_key(user: dict, encrypted: str, hint: str) -> dict:
    row = await db.pool.fetchrow("UPDATE users SET claude_key = $2, claude_key_hint = $3, claude_key_at = $4 WHERE id = $1 RETURNING *",
                                 user["id"], encrypted, hint, time.time())
    log.info("%s saved a Claude API key", user["email"])
    return public(row)


async def delete_claude_key(user: dict) -> dict:
    row = await db.pool.fetchrow("UPDATE users SET claude_key = '', claude_key_hint = '', claude_key_at = NULL WHERE id = $1 RETURNING *",
                                 user["id"])
    return public(row)


async def set_run_limit(user: dict, value) -> dict:
    p = plans.of(user)
    if value in (None, ""):
        amount = None
    else:
        try:
            amount = round(float(value), 2)
        except (TypeError, ValueError):
            raise AuthError("The limit is an amount in US dollars, like 5.")
        if not 0.5 <= amount <= p["run_limit_max"]:
            raise AuthError(f"Pick a limit between $0.50 and ${p['run_limit_max']:g} on the {p['name']} plan.")
    row = await db.pool.fetchrow("UPDATE users SET run_limit = $2 WHERE id = $1 RETURNING *", user["id"], amount)
    return public(row)


async def stored_claude_key(user_id) -> str:
    """The encrypted key of this member ('' if none)."""
    return await db.pool.fetchval("SELECT claude_key FROM users WHERE id = $1", user_id) or ""
