"""Settings for Agents Atelier, in one place.

Values come from, in order of priority:
  1. real environment variables (e.g. `PORT=9000 ./run.sh`, or what the tests set),
  2. the `.env` file at the project root (copy `.env.example` to `.env` and edit it),
  3. the defaults below.

The .env parser is deliberately small (no extra dependency): one KEY=value per line, `#` comments,
optional `export ` prefix and optional single or double quotes around the value.
"""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.environ.get("ATELIER_ENV_FILE") or ROOT / ".env")
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def load_env(path: Path = ENV_FILE) -> dict[str, str]:
    """Read `path` and put its values into os.environ, without overriding variables that are already set."""
    found: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, PermissionError, UnicodeDecodeError):
        return found
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = _LINE.match(raw)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.sub(r"\s+#.*$", "", value)   # trailing comment on an unquoted value
        found[key] = value
        os.environ.setdefault(key, value)
    return found


load_env()


def _int(name: str, default: int, low: int = 1) -> int:
    try:
        return max(low, int(os.environ.get(name, "").strip() or default))
    except ValueError:
        raise SystemExit(f"Setting {name} must be a whole number, got {os.environ.get(name)!r} (check your .env).")


def _mb(name: str, default: float) -> int:
    try:
        return int(float(os.environ.get(name, "").strip() or default) * 1024 * 1024)
    except ValueError:
        raise SystemExit(f"Setting {name} must be a number of megabytes, got {os.environ.get(name)!r} (check your .env).")


def _path(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    p = Path(value).expanduser()
    return p if p.is_absolute() else ROOT / p


# ---- server
HOST = os.environ.get("HOST", "").strip() or "127.0.0.1"
PORT = _int("PORT", 8001)
ALLOWED_HOSTS = set(filter(None, (h.strip() for h in os.environ.get("ATELIER_HOSTS", "").split(","))))
LOG_LEVEL = (os.environ.get("LOG_LEVEL", "").strip() or "INFO").upper()

# ---- folders
# Every file the app keeps: agents/<agent-uuid>/ and works/<project-uuid>/ (see storage.py). Metadata is in Postgres.
STORAGE_DIR = _path("STORAGE_DIR", ROOT / "storage")
# The folder of earlier versions (one JSON file per project). Imported into STORAGE_DIR and Postgres once, then left alone.
LEGACY_WORK_DIR = _path("ATELIER_WORK", ROOT / "work")
AGENTS_DIR = _path("ATELIER_AGENTS", ROOT / "agents")

# ---- Claude Code
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "").strip() or "claude"
AGENT_MODEL = os.environ.get("AGENT_MODEL", "").strip() or None
AGENT_TIMEOUT = _int("AGENT_TIMEOUT", 900)

# ---- limits
MAX_BODY = _mb("MAX_BODY_MB", 8)
MAX_IMAGE = _mb("MAX_IMAGE_MB", 5)
MAX_SITE_IMPORT = _mb("MAX_SITE_IMPORT_MB", 40)   # largest existing site you can start a project from
MAX_EDIT = int(float(os.environ.get("MAX_EDIT_KB", "").strip() or 500) * 1024)
MAX_VERSIONS = _int("MAX_VERSIONS", 150)
# ---- plan
# "free" limits the costly parts of a run: the Critic reviews once and can't send work back.
PLAN = (os.environ.get("ATELIER_PLAN", "").strip() or "pro").lower()
if PLAN not in ("free", "pro"):
    raise SystemExit(f"Setting ATELIER_PLAN must be free or pro, got {PLAN!r} (check your .env).")
# times the Critic may send work back to earlier agents in one run (always 0 on the free plan)
REVIEW_ROUNDS = 0 if PLAN == "free" else _int("REVIEW_ROUNDS", 2, low=0)

# ---- whose Claude account pays for the agents
# "user": every member adds their own Claude API key (profile page); runs use the project owner's key, and nothing
#         runs without one. The server's own Claude login is never used for members' projects.
# "server": the agents use the Claude Code login of the machine running the server (a private, self-hosted install).
CLAUDE_KEYS = (os.environ.get("CLAUDE_KEYS", "").strip() or "user").lower()
if CLAUDE_KEYS not in ("user", "server"):
    raise SystemExit(f"Setting CLAUDE_KEYS must be user or server, got {CLAUDE_KEYS!r} (check your .env).")
# encrypts the members' API keys in the database (a Fernet key). Empty: one is made and kept in STORAGE_DIR/.secrets/.
KEY_SECRET = os.environ.get("KEY_SECRET", "").strip()

# ---- account deletion
# "Delete my account" deactivates it; everything is removed this many days later unless the member logs in again.
ACCOUNT_DELETE_DAYS = _int("ACCOUNT_DELETE_DAYS", 30, low=0)
# how often the server looks for accounts whose time is up
ACCOUNT_PURGE_SECONDS = _int("ACCOUNT_PURGE_SECONDS", 3600, low=1)

# ---- database (Postgres: users, sessions, email codes, agents, teams) — see database/docker-compose.yaml
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip() or "postgresql://atelier:atelier@127.0.0.1:5432/atelier"

# ---- accounts
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()   # this account becomes admin once confirmed
SESSION_DAYS = _int("SESSION_DAYS", 30)
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes")   # set it when served over HTTPS
CODE_MINUTES = _int("CODE_MINUTES", 15)   # how long an email confirmation code works

# ---- email (confirmation codes). Without SMTP_HOST, codes are written to the server log (local testing).
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = _int("SMTP_PORT", 587)
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "").strip() or SMTP_USER or "Agents Atelier <no-reply@localhost>"
SMTP_SECURITY = (os.environ.get("SMTP_SECURITY", "").strip() or "starttls").lower()   # starttls · ssl · none
if SMTP_SECURITY not in ("starttls", "ssl", "none"):
    raise SystemExit(f"Setting SMTP_SECURITY must be starttls, ssl or none, got {SMTP_SECURITY!r} (check your .env).")
EMAIL_OUTBOX = _path("EMAIL_OUTBOX", Path()) if os.environ.get("EMAIL_OUTBOX", "").strip() else None   # also append emails to this file (JSON lines)

def _yes(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return default if not value else value in ("1", "true", "yes", "on")


# ---- sign-up protection (per IP address; the limits reset as time passes)
TRUST_PROXY = _yes("TRUST_PROXY", False)          # behind a reverse proxy: read the visitor's IP from X-Forwarded-For
SIGNUPS_PER_HOUR = _int("SIGNUPS_PER_HOUR", 5, low=0)          # new accounts per IP per hour (0 = no limit)
LOGINS_PER_15_MIN = _int("LOGINS_PER_15_MIN", 30, low=0)       # login attempts per IP per 15 minutes
RESETS_PER_HOUR = _int("RESETS_PER_HOUR", 10, low=0)           # password reset requests per IP per hour
BLOCK_DISPOSABLE_EMAILS = _yes("BLOCK_DISPOSABLE_EMAILS", True)
DISPOSABLE_DOMAINS_FILE = _path("DISPOSABLE_DOMAINS_FILE", Path()) if os.environ.get("DISPOSABLE_DOMAINS_FILE", "").strip() else None   # extra domains, one per line

# ---- real browser checks before the QA agent and the Critic (needs Chrome or Chromium on the server)
BROWSER_CHECKS = _yes("BROWSER_CHECKS", True)
CHROME_BIN = os.environ.get("CHROME_BIN", "").strip()          # empty = look for google-chrome / chromium
CHROME_NO_SANDBOX = _yes("CHROME_NO_SANDBOX", False)        # inside a container, where Chrome's own sandbox can't start

# ---- legal pages (shown on /terms and /privacy)
LEGAL_NAME = os.environ.get("LEGAL_NAME", "").strip()          # who runs this service, e.g. "Atelier SARL"
LEGAL_EMAIL = os.environ.get("LEGAL_EMAIL", "").strip().lower() or ADMIN_EMAIL
LEGAL_ADDRESS = os.environ.get("LEGAL_ADDRESS", "").strip()

# ---- project brief & assets (asked when a project is created)
MAX_ASSETS = _int("MAX_ASSETS", 12)   # logo + photos a project can start with
