"""Which Claude models an agent may use.

An agent's `model:` must be one of:
  - an alias the installed Claude CLI advertises in `claude --help` (e.g. fable, opus, sonnet)
  - a predefined full model name (KNOWN_FULL below)
  - a custom name that was checked against the Claude CLI (stored in agents/_models.json)
or empty, meaning the server default (AGENT_MODEL, or Claude Code's own setting).
"""

import asyncio
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import config

AGENTS_DIR = config.AGENTS_DIR
VERIFIED_FILE = AGENTS_DIR / "_models.json"
CLAUDE = config.CLAUDE_BIN

FALLBACK_ALIASES = ["fable", "opus", "sonnet"]
KNOWN_FULL = [
    ("claude-fable-5-1", "Fable 5.1"),
    ("claude-opus-5-5", "Opus 5.5"),
    ("claude-sonnet-5-5", "Sonnet 5.5"),
    ("claude-haiku-5-5", "Haiku 5.5"),
]
ALIAS_NOTES = {
    "opus": "strongest: planning, design and review",
    "sonnet": "fast and capable: good for building steps",
    "fable": "latest Fable model",
    "haiku": "fastest and cheapest: small tasks",
}
NAME_RE = re.compile(r"^[A-Za-z0-9._\-\[\]]{1,64}$")

_aliases_cache: dict = {}


def cli_aliases() -> tuple[list[str], str]:
    """Aliases advertised by `claude --help`. Returns (aliases, source)."""
    if "value" in _aliases_cache:
        return _aliases_cache["value"]
    try:
        out = subprocess.run([CLAUDE, "--help"], capture_output=True, text=True, timeout=15).stdout
        text = " ".join(out.split())
        m = re.search(r"alias for the latest model \(e\.g\.(.*?)\)", text)
        aliases = re.findall(r"'([a-z0-9.\-]+)'", m.group(1)) if m else []
        value = (aliases, "claude --help") if aliases else (FALLBACK_ALIASES, "built-in list")
    except (OSError, subprocess.SubprocessError):
        value = (FALLBACK_ALIASES, "built-in list (Claude CLI not found)")
    _aliases_cache["value"] = value
    return value


def verified() -> list[dict]:
    try:
        return json.loads(VERIFIED_FILE.read_text()) if VERIFIED_FILE.exists() else []
    except ValueError:
        return []


def _save_verified(items: list[dict]):
    tmp = VERIFIED_FILE.with_name(VERIFIED_FILE.name + ".tmp")
    tmp.write_text(json.dumps(items, indent=2))
    os.replace(tmp, VERIFIED_FILE)


def catalog() -> dict:
    aliases, source = cli_aliases()
    options = [{"id": a, "label": a, "kind": "alias", "source": source, "note": ALIAS_NOTES.get(a, "alias for the latest model")}
               for a in aliases]
    seen = {o["id"] for o in options}
    for mid, label in KNOWN_FULL:
        if mid not in seen:
            options.append({"id": mid, "label": label, "kind": "full", "source": "predefined", "note": "a specific model version"})
            seen.add(mid)
    for v in verified():
        if v["name"] not in seen:
            options.append({"id": v["name"], "label": v["name"], "kind": "custom", "source": "checked with the Claude CLI",
                            "note": f"resolves to {v.get('resolved') or v['name']}"})
            seen.add(v["name"])
    env = config.AGENT_MODEL or ""
    default = {"id": "", "label": f"Default ({env})" if env else "Default (your Claude Code model)",
               "note": "AGENT_MODEL is set on the server" if env else "whatever model Claude Code uses by default"}
    return {"default": default, "options": options, "alias_source": source}


def allowed(name: str) -> bool:
    return not name or name in {o["id"] for o in catalog()["options"]}


async def check(name: str, timeout: int = 90, env: dict | None = None) -> dict:
    """Ask the Claude CLI to run a one-word reply with this model. Costs a tiny call when the model is valid."""
    name = name.strip()
    if not NAME_RE.match(name):
        raise ValueError("A model name is letters, numbers, dots, dashes or brackets (max 64).")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            proc = await asyncio.create_subprocess_exec(
                CLAUDE, "-p", "Reply with the single word OK.", "--model", name, "--output-format", "json",
                "--restricted", "--strict-mcp-config", "--disable-slash-commands", "--tools", "",
                cwd=tmp, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except FileNotFoundError:
            raise ValueError(f"Claude Code wasn't found (tried “{CLAUDE}”), so the model can't be checked.")
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise ValueError("The Claude CLI didn't answer in time. Try again.")
    text = out.decode(errors="replace")
    errors = err.decode(errors="replace") + text
    if "unrecognized_model" in errors:
        raise ValueError(f"The Claude CLI doesn't recognise the model “{name}”.")
    try:
        data = json.loads(text.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise ValueError(f"The Claude CLI couldn't use “{name}”: {errors.strip()[-200:] or 'no answer'}")
    resolved = list((data.get("modelUsage") or {}).keys())
    if data.get("is_error") or not resolved:
        raise ValueError(f"The Claude CLI couldn't use “{name}”: {str(data.get('result') or errors).strip()[-200:]}")
    entry = {"name": name, "resolved": resolved[0], "checked": time.time()}
    known = {o["id"] for o in catalog()["options"] if o["kind"] != "custom"}
    if name not in known:
        _save_verified([v for v in verified() if v["name"] != name] + [entry])
    return {**entry, "cost": data.get("total_cost_usd") or 0, "already_listed": name in known}
