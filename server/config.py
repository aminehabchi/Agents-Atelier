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
WORK_DIR = _path("ATELIER_WORK", ROOT / "work")
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
