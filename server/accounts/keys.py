"""Each member's own Claude API key (CLAUDE_KEYS=user).

The key is checked with one tiny call, then kept encrypted (Fernet, KEY_SECRET) in users.claude_key. Only its last
four characters ever reach the browser. Runs use the project owner's key: the server starts each `claude -p` with a
clean environment holding that key and a per-member Claude config folder, so the server's own Claude login, other
members' keys and the server's settings are never visible to an agent.
"""

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from core import config

KEY_RE = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,250}$")
USER_MODE = config.CLAUDE_KEYS == "user"
# what a claude process may inherit from the server (no secrets: DATABASE_URL, SMTP_*, KEY_SECRET… stay out)
SAFE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "NODE_OPTIONS", "FAKE_CLAUDE_LOG",
            "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy", "SSL_CERT_FILE")


class KeyError_(ValueError):
    """A key that can't be saved or used; the message is safe to show (it never contains the key)."""


def _secret() -> bytes:
    if config.KEY_SECRET:
        return config.KEY_SECRET.encode()
    folder = Path(config.STORAGE_DIR) / ".secrets"
    path = folder / "key_secret"
    if not path.is_file():
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(Fernet.generate_key())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    return path.read_bytes().strip()


_fernet = None


def fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        try:
            _fernet = Fernet(_secret())
        except ValueError:
            raise SystemExit("KEY_SECRET isn't a valid Fernet key (44 characters of URL-safe base64). See .env.example.")
    return _fernet


def clean(key) -> str:
    key = "".join(str(key or "").split())
    if not KEY_RE.match(key):
        raise KeyError_("That doesn't look like a Claude API key. It starts with sk-ant- (create one at console.anthropic.com).")
    return key


def encrypt(key: str) -> str:
    return fernet().encrypt(key.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return ""            # KEY_SECRET changed: the member adds the key again


def hint(key: str) -> str:
    return "sk-ant-…" + key[-4:]


def scrub(text: str, key: str) -> str:
    """Never echo a key back, even inside an error message."""
    text = str(text or "")
    if key:
        text = text.replace(key, "[your key]")
    return re.sub(r"sk-ant-[A-Za-z0-9_\-]{8,}", "[a key]", text)


def env_for(user_id: str, key: str) -> dict:
    """The whole environment of one claude process for this member."""
    home = Path(config.STORAGE_DIR) / "claude-home" / str(user_id)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = {k: os.environ[k] for k in SAFE_ENV if k in os.environ}
    # HOME too: the server account's own ~/.claude login is never in reach
    env.update(ANTHROPIC_API_KEY=key, CLAUDE_CONFIG_DIR=str(home), HOME=str(home), DISABLE_AUTOUPDATER="1",
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    return env


def explain(text: str) -> str | None:
    """A friendly reason when Claude refused the key itself, else None."""
    low = text.lower()
    if "invalid x-api-key" in low or "authentication_error" in low or "invalid api key" in low or "401" in low:
        return "Claude refused this API key. Check it at console.anthropic.com, or create a new one, then save it in your profile."
    if "credit balance is too low" in low or "billing" in low:
        return "Your Claude account has no credit left. Add credit at console.anthropic.com, then try again."
    if "permission_error" in low or "403" in low:
        return "This API key isn't allowed to use Claude's models. Check its workspace at console.anthropic.com."
    return None


async def check(key: str, user_id: str, timeout: int = 90) -> dict:
    """One tiny call with the key. Raises KeyError_ with a safe message when it doesn't work."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            proc = await asyncio.create_subprocess_exec(
                config.CLAUDE_BIN, "-p", "Reply with the single word OK.", "--model", "haiku", "--output-format", "json",
                "--restricted", "--strict-mcp-config", "--disable-slash-commands", "--tools", "",
                cwd=tmp, env=env_for(user_id, key), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except (FileNotFoundError, PermissionError):
            raise KeyError_("The server can't start Claude Code, so the key can't be checked. Tell the site's admin.")
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise KeyError_("Claude didn't answer in time. Try again in a moment.")
    text = out.decode(errors="replace")
    errors = scrub(err.decode(errors="replace") + text, key)
    try:
        data = json.loads(text.strip().splitlines()[-1])
    except (ValueError, IndexError):
        data = {}
    data = data if isinstance(data, dict) else {}
    if not data or data.get("is_error"):
        raise KeyError_(explain(errors) or f"Claude couldn't use this key: {scrub(str(data.get('result') or errors), key).strip()[-200:] or 'no answer'}")
    return {"cost": data.get("total_cost_usd") or 0}
