"""The Orchestrator: decides which agents work on a prompt.

Flow for every prompt (see run_prompt in main.py):
  1. A project that was never built runs the whole team (the agents switched on): no model call needed.
  2. Otherwise the Orchestrator (a read-only `claude -p` run, prompt in agents/_orchestrator.md) reads the
     request and the project state and answers with one JSON decision.
  3. decide() checks that decision with hard rules the model can't break: only switched-on agents, the Brief
     agent always first, dependent agents added (e.g. HTML changes → CSS), QA and the Critic after code changes.
     A missing or broken answer falls back to the whole team, so a run is never blocked.

Everything here is pure (no I/O besides reading files) so it can be tested without a model.
"""

import json
import re
from pathlib import Path

import config
from agents import _parse_text

PROMPT_FILE = config.AGENTS_DIR / "_orchestrator.md"
BRIEF = "brief"                         # always runs whenever any agent runs
FINISHERS = ("qa", "critic")            # run after any change to the code, when switched on
READ_ONLY = {"brief", "critic", "qa", "research"}   # agents whose choice alone doesn't count as a code change
ON_REQUEST = {"research"}   # may be chosen when the user asks for it, even if switched off (never in a fallback)
INTENTS = {"new_build", "change", "redo_agent", "question", "off_topic", "unclear"}
TALK = {"question", "off_topic", "unclear"}   # intents where no agent runs: the Orchestrator answers in the chat
# When an agent's work changes, these agents must adapt to it (only added if switched on).
DEPENDS = {
    "ux": ["copy", "html", "css"],
    "design": ["css"],
    "html": ["css"],
    "assets": ["html"],
}
STATE_LIMIT = 4000   # characters of plan.md shown to the Orchestrator


def load_prompt() -> tuple[str, str]:
    """(role prompt, model) from agents/_orchestrator.md; a built-in fallback if the file is missing."""
    if PROMPT_FILE.exists():
        meta, role = _parse_text(PROMPT_FILE.read_text())
        if role:
            return role, str(meta.get("model") or "")
    return ("You are the Orchestrator. Pick the agents needed for the request. Reply with only a JSON object: "
            '{"intent": "change", "agents": [], "notes": {}, "reason": "", "reply": null}'), ""


def is_fresh(meta: dict, folder: Path) -> bool:
    """Never built: no earlier request, not imported, and no home page yet. The whole team works."""
    return not meta.get("history") and not meta.get("imported") and not (folder / "index.html").exists()


def project_state(meta: dict, folder: Path, clean) -> str:
    """What the Orchestrator needs to know about the project, as text. `clean` defuses untrusted text."""
    files = []
    if folder.is_dir():
        for f in sorted(folder.rglob("*")):
            rel = f.relative_to(folder)
            if f.is_file() and not any(p.startswith(".") for p in rel.parts) and rel.parts[0] != "uploads":
                files.append(f"- {rel} ({f.stat().st_size} bytes)")
            if len(files) >= 60:
                files.append("- …")
                break
    pages = meta.get("pages") or [{"file": "index.html", "title": "Home"}]
    missing = [p["file"] for p in pages if not (folder / p["file"]).exists()]
    plan = folder / "plan.md"
    plan_text = clean(plan.read_text(errors="replace"), STATE_LIMIT) if plan.is_file() else "(no plan.md yet)"
    run = meta.get("run") or {}
    notes = "\n".join(clean(n, 300) for n in (run.get("notes") or [])[-16:]) or "(none)"
    ex = meta.get("last_exchange") or {}
    asked = (f"\n\nYour previous answer to the user (their new request may be a reply to it):\n"
             f"They asked: {clean(ex.get('request'), 500)}\nYou answered: {clean(ex.get('reply'), 800)}") if ex.get("reply") else ""
    return (f"Files:\n{chr(10).join(files) or '(none)'}\n\n"
            f"Pages not built yet: {', '.join(missing) or 'none'}\n\n"
            f"plan.md:\n{plan_text}\n\n"
            f"Last run: {clean(run.get('status') or 'none', 20)}, request: {clean(run.get('request') or '(none)', 300)}\n"
            f"What each agent reported in the last run:\n{notes}{asked}")


def build_prompt(meta: dict, request: str, team: list[dict], folder: Path, clean, site_pages: str, brief: str = "") -> str:
    past = "\n".join(f"- {clean(h, 300)}" for h in meta.get("history", [])[-8:]) or "(none)"
    roster = "\n".join(f"- {a['id']}: {a['name']} — {clean(a.get('description') or '', 160)}"
                       + (" (switched off: choose it only when the user asks for research)" if a["id"] in ON_REQUEST
                          and a["id"] not in (meta.get("agents") or []) else "") for a in team)
    return f"""<project>
name: {clean(meta.get("name"), 80)}
description: {clean(meta.get("description") or "(no description)", 300)}
</project>
{brief}
<site_pages>
{site_pages}
</site_pages>

<earlier_requests>
{past}
</earlier_requests>

<project_state>
{project_state(meta, folder, clean)}
</project_state>

<team>
{roster}
</team>

<user_request>
{clean(request, 10000)}
</user_request>

Decide now. Reply with only the JSON object."""


def parse(text: str) -> dict | None:
    """The JSON object in the Orchestrator's reply (tolerates a code fence or words around it)."""
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _short(value, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit].rstrip() + "…" if len(text) > limit else text


def decide(raw: dict | None, pool: list[str], order: list[str]) -> dict:
    """Turn the model's answer into a safe plan.

    pool:  the agents switched on for this project (the only ones the Orchestrator may choose; Brief is always allowed)
    order: every agent id in team order
    Returns {intent, agents, notes, reason, reply, fallback}. `agents` is empty when the Orchestrator only answers.
    """
    allowed = set(pool) | {BRIEF}
    everyone = [i for i in order if i in allowed]
    choosable = allowed | ON_REQUEST
    if not raw:
        return {"intent": "new_build", "agents": everyone, "notes": {}, "fallback": True, "reply": None,
                "reason": "The Orchestrator couldn't decide, so the whole team works on it."}
    intent = raw.get("intent") if raw.get("intent") in INTENTS else "change"
    reply = _short(raw.get("reply"), 1200) or None
    reason = _short(raw.get("reason"), 300)
    chosen = {i for i in (raw.get("agents") if isinstance(raw.get("agents"), list) else []) if isinstance(i, str) and i in choosable}

    if intent in TALK or (not chosen and intent != "new_build"):
        if not reply:   # nothing to say and nothing to do: ask instead of guessing
            intent, reply = "unclear", "What would you like the team to change? For example: a section, the colours, the text or a page."
        return {"intent": intent if intent in TALK else "unclear", "agents": [], "notes": {}, "reason": reason,
                "reply": reply, "fallback": False}

    if intent == "new_build":
        chosen = set(everyone)
    # hard rules: dependents adapt, Brief always runs, QA + Critic check any code change
    for _ in range(3):
        for a in list(chosen):
            chosen |= {d for d in DEPENDS.get(a, []) if d in allowed}
    chosen.add(BRIEF)
    if chosen - READ_ONLY:
        chosen |= {f for f in FINISHERS if f in allowed}
    agents = [i for i in order if i in chosen]
    notes = raw.get("notes") if isinstance(raw.get("notes"), dict) else {}
    notes = {k: _short(v, 400) for k, v in notes.items() if k in chosen and _short(v, 400)}
    return {"intent": intent, "agents": agents, "notes": notes, "reason": reason or "These agents cover your request.",
            "reply": None, "fallback": False}
