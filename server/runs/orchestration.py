"""Every prompt runs in this order: the Brief agent first (it reads the prompt and updates ## Brief in plan.md), then the
Orchestrator (it reads what the Brief wrote and decides who works, asks, or answers), then the agents it chose.

  run_prompt         starts a run with the Brief alone; the plan is "deferred" until the Brief is done
  after_brief        called by run_chain once the Brief finished: asks the Orchestrator and returns the rest of the
                     plan, or None when nothing else runs (an answer, questions, a plan to confirm, contact details)
  start_plan         runs a decided plan (after a yes or contact details), without running the Brief a second time
  run_orchestrator   one read-only `claude -p` call; on a first build it only chooses which agents work side by side
"""

import asyncio
import json
import time

from crew import models as model_catalog
from crew import orchestrator as orch
from site_tools import contact

from state import (CLAUDE, MODEL, ORCH_TIMEOUT, broadcast, live, load_meta, log, project_agents, record_stat, safely,
                   save_meta, site_dir)
from runs.prompts import PROMPT_LIMIT, SECURITY_RULES, brief_block, clean_text, language_rule, site_pages
from runs.safety import DENY_READS, injection_note, outside_project, scrub_paths
from runs.pipeline import NoClaudeKey, _stop_escape, claude_env, explain_failure, run_chain


class OrchestratorUnavailable(RuntimeError):
    """Claude Code itself can't run (not installed, not logged in, rate-limited): no point running the team either."""


async def run_orchestrator(pid: str, request: str, team: list[dict], meta: dict, first_build: bool = False,
                           brief_result: str = "") -> dict | None:
    """Ask the Orchestrator (read-only `claude -p`) which agents to run. Returns its parsed JSON, or None.
    With first_build, it only chooses which of the team's agents may work at the same time."""
    folder = site_dir(pid)
    role, model = orch.load_prompt()
    model = model if model and model_catalog.allowed(model) else MODEL
    prompt = orch.build_prompt(meta, request, team, folder, clean_text, site_pages(meta), brief_block(meta), first_build=first_build,
                               brief_result=brief_result)
    note = await injection_note(pid, folder, request)       # it reads the files too: it gets the same warning
    if note:
        prompt += "\n\n" + note
    cmd = [CLAUDE, "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--restricted", "--strict-mcp-config", "--disable-slash-commands",
           "--tools", "Read,Glob,Grep", "--allowedTools", "Read", "Glob", "Grep",
           "--disallowedTools", "Write", "Edit", "Bash", "WebFetch", "WebSearch", "Task", *DENY_READS,
           "--max-turns", "8", "--exclude-dynamic-system-prompt-sections",
           "--append-system-prompt", await language_rule(meta) + role + "\n\n" + SECURITY_RULES]
    if model:
        cmd += ["--model", model]
    folder.mkdir(parents=True, exist_ok=True)
    try:
        env = await claude_env(meta)
    except NoClaudeKey as e:
        raise OrchestratorUnavailable(str(e))
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=folder, env=env, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024)
    except (FileNotFoundError, PermissionError):
        raise OrchestratorUnavailable(f"Claude Code wasn't found or isn't executable (tried “{CLAUDE}”). "
                                      "Install it, or set CLAUDE_BIN to its path, then restart the server.")
    st = live(pid)
    st["procs"].add(proc)
    started, info, text, outcome = time.time(), {"model": model or ""}, "", "error"

    async def read():
        nonlocal text
        async for raw in proc.stdout:
            try:
                ev = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("type") == "system" and ev.get("model"):
                info["model"] = ev["model"]
            elif ev.get("type") == "assistant":
                for block in (ev.get("message") or {}).get("content") or []:
                    escape = isinstance(block, dict) and block.get("type") == "tool_use" and \
                        outside_project(pid, folder, block.get("name"), block.get("input") or {})
                    if escape:
                        await _stop_escape(pid, {"id": "orchestrator", "name": "Orchestrator"}, proc,
                                           f"{block.get('name')}: {clean_text(scrub_paths(escape), 160)}")
            elif ev.get("type") == "result":
                info.update(cost=round(ev.get("total_cost_usd") or 0, 5), turns=ev.get("num_turns"))
                if ev.get("is_error"):
                    raise RuntimeError(explain_failure(ev.get("result") or ""))
                text = ev.get("result") or text
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(explain_failure((await proc.stderr.read()).decode(errors="replace")))

    try:
        await asyncio.wait_for(read(), timeout=ORCH_TIMEOUT)
        outcome = "done"
    except asyncio.TimeoutError:
        log.warning("Orchestrator timed out on project %s", pid)
        return None
    except RuntimeError as e:
        msg = str(e)
        if any(k in msg for k in ("isn't logged in", "rate-limited", "API key", "no credit left")):
            raise OrchestratorUnavailable(msg)
        log.warning("Orchestrator failed on project %s: %s", pid, msg)
        return None
    except asyncio.CancelledError:
        outcome = "stopped"
        raise
    finally:
        if proc.returncode is None:
            proc.kill()
        st["procs"].discard(proc)
        safely("Recording stats", record_stat, pid, {"agent": "orchestrator", "status": outcome, "ts": started,
                                                     "request": request[:100], "seconds": round(time.time() - started, 1), **info})
    decision = orch.parse(text)
    if decision is None:
        log.warning("Orchestrator gave no usable JSON on project %s: %r", pid, text[:300])
    return decision


async def run_prompt(pid: str, request: str, pool: list[str], images: list[str]):
    """A new prompt. The Brief agent works on it first; when it's done, run_chain calls after_brief, where the Orchestrator
    reads the brief and decides the rest. (A project without the Brief agent goes straight to the Orchestrator.)"""
    meta = load_meta(pid)
    order = [a["id"] for a in await project_agents(meta)]
    first = orch.is_fresh(meta, site_dir(pid))
    if orch.BRIEF in order:
        # the last exchange (the Orchestrator's question, if any) stays until it decides again: it reads it to know this
        # prompt answers it, then settle() replaces it
        plan = {"deferred": True, "pool": list(pool), "first": first, "agents": [orch.BRIEF], "notes": {}}
        await run_chain(pid, request, [orch.BRIEF], images=images, plan=plan)
        return
    plan = await decide_plan(pid, request, pool, first, "")
    if plan is not None and await settle(pid, request, plan, images):
        await start_plan(pid, request, plan, images)


async def decide_plan(pid: str, request: str, pool: list[str], first: bool, brief_result: str) -> dict | None:
    """The Orchestrator's plan for this prompt (None when it couldn't run at all: an error was shown)."""
    meta = load_meta(pid)
    agents = await project_agents(meta)
    order = [a["id"] for a in agents]
    if first:                                   # a first build: the whole team; the Orchestrator only pairs agents up
        everyone = [i for i in order if i in set(pool) | {orch.BRIEF}]
        plan = {"intent": "new_build", "agents": everyone, "notes": {}, "reply": None, "fallback": False, "first": True,
                "reason": "First build: the whole team works on it."}
        team = [a for a in agents if a["id"] in everyone]
        if len(team) >= 3 and orch.could_pair(everyone):
            await broadcast(pid, {"type": "orchestrating", "first": True})
            try:
                raw = await run_orchestrator(pid, request, team, meta, first_build=True, brief_result=brief_result)
                plan["parallel"] = orch.clean_parallel((raw or {}).get("parallel"), everyone)
            except asyncio.CancelledError:
                raise
            except Exception as e:              # pairing is a bonus: one agent after another is fine
                log.warning("Couldn't plan parallel steps for %s: %s", pid, e)
        return plan
    await broadcast(pid, {"type": "orchestrating"})
    try:
        team = [a for a in agents if a["id"] in set(pool) | {orch.BRIEF} | orch.ON_REQUEST]
        return orch.decide(await run_orchestrator(pid, request, team, meta, brief_result=brief_result), pool, order)
    except asyncio.CancelledError:
        raise
    except OrchestratorUnavailable as e:
        await broadcast(pid, {"type": "error", "text": str(e), "orchestrator": True})
        return None
    except Exception:   # never block the user: fall back to the whole team
        log.exception("Orchestrator crashed on project %s", pid)
        return orch.decide(None, pool, order)


async def settle(pid: str, request: str, plan: dict, images: list[str]) -> bool:
    """Show the plan and keep what must wait for the user (an answer, questions, a yes, contact details).
    True when its agents can start now."""
    keys = contact.missing(plan["agents"], load_meta(pid)) if plan["agents"] and not plan.get("confirm") else []
    if keys:
        await ask_contact(pid, request, plan, images, keys)
        return False
    await broadcast(pid, {"type": "plan", **plan})
    meta = load_meta(pid)
    # The Orchestrator answered, asked (a question or a short form) or wants a yes first: keep that exchange (not in the
    # history the agents read), so the user's next prompt or the form's answers can simply follow it. A run clears it.
    waiting = not plan["agents"] or plan.get("confirm")
    asked = plan.get("reply") or "\n".join(q["text"] for q in plan.get("questions") or [])
    meta["last_exchange"] = {"request": request[:PROMPT_LIMIT], "reply": asked, "questions": plan.get("questions") or [],
                             "images": images} if waiting else None
    meta["pending"] = {"request": request, "plan": plan, "images": images} if plan.get("confirm") else None
    save_meta(meta)
    return bool(plan["agents"]) and not plan.get("confirm")


async def after_brief(pid: str, request: str, plan: dict, images: list[str], brief_result: str) -> dict | None:
    """The Brief is done: the Orchestrator decides the rest from what it wrote. Returns the plan for the remaining agents
    (the Brief already ran, so it isn't run again), or None when nothing else runs now."""
    decided = await decide_plan(pid, request, plan.get("pool") or [], bool(plan.get("first")), brief_result)
    if decided is None:
        return None
    decided["brief_done"] = True             # a plan confirmed later, or started after contact details, skips the Brief
    if not await settle(pid, request, decided, images):
        return None
    return decided


async def ask_contact(pid: str, request: str, plan: dict, images: list[str], keys: list[str]):
    """The plan's agents need contact details the project doesn't have: ask for them first (the answers start this same
    plan, see handle_answer)."""
    qs = contact.questions(keys)
    reply = ("Before the team starts, a few contact details. Skip any you don't have yet: "
             "its button stays hidden until you add it.")
    await broadcast(pid, {"type": "plan", **plan, "confirm": False, "questions": qs, "needs": keys, "reply": reply})
    meta = load_meta(pid)
    meta["pending"] = {"request": request, "plan": {**plan, "confirm": False}, "images": images}
    meta["last_exchange"] = {"request": request[:PROMPT_LIMIT], "reply": reply, "questions": qs, "images": images, "needs": keys}
    save_meta(meta)


async def start_plan(pid: str, request: str, plan: dict, images: list[str]):
    """Run a decided plan, unless its agents need contact details the project doesn't have: then ask for them first
    (the answers start this same plan, see handle_answer). A plan decided after the Brief doesn't run the Brief again."""
    if plan.get("brief_done"):
        plan = {**plan, "agents": [i for i in plan["agents"] if i != orch.BRIEF]}
        if not plan["agents"]:
            return
    keys = contact.missing(plan["agents"], load_meta(pid))
    if not keys:
        schedule = None
        if plan.get("first") and not plan.get("parallel") and orch.could_pair(plan["agents"]):
            schedule = asyncio.create_task(schedule_first_build(pid, request, plan["agents"]))
        await run_chain(pid, request, plan["agents"], images=images, plan=plan, schedule=schedule)
        return
    await ask_contact(pid, request, plan, images, keys)


async def schedule_first_build(pid: str, request: str, agent_ids: list[str]) -> list[list[str]]:
    """A first build runs the whole team without asking the Orchestrator; meanwhile (Brief works first, alone) it is
    asked which agents may work at the same time. Any failure just means one agent after another."""
    meta = load_meta(pid)
    team = [a for a in await project_agents(meta) if a["id"] in agent_ids]
    if len(team) < 3:
        return []
    try:
        raw = await run_orchestrator(pid, request, team, meta, first_build=True)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning("Couldn't plan parallel steps for %s: %s", pid, e)
        return []
    return orch.clean_parallel((raw or {}).get("parallel"), [a["id"] for a in team])
