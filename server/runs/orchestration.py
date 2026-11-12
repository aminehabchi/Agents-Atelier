"""Asking the Orchestrator which agents a prompt needs (run_orchestrator), then starting that plan: a new prompt
(run_prompt), a decided plan (start_plan) and the parallel steps of a first build (schedule_first_build)."""

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


async def run_orchestrator(pid: str, request: str, team: list[dict], meta: dict, first_build: bool = False) -> dict | None:
    """Ask the Orchestrator (read-only `claude -p`) which agents to run. Returns its parsed JSON, or None.
    With first_build, it only chooses which of the team's agents may work at the same time."""
    folder = site_dir(pid)
    role, model = orch.load_prompt()
    model = model if model and model_catalog.allowed(model) else MODEL
    prompt = orch.build_prompt(meta, request, team, folder, clean_text, site_pages(meta), brief_block(meta), first_build=first_build)
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
    """A new prompt: decide who works (Orchestrator), then run them. The Brief agent always runs when anyone does."""
    meta = load_meta(pid)
    agents = await project_agents(meta)
    order = [a["id"] for a in agents]
    if orch.is_fresh(meta, site_dir(pid)):
        everyone = [i for i in order if i in set(pool) | {orch.BRIEF}]
        plan = {"intent": "new_build", "agents": everyone, "notes": {}, "reply": None, "fallback": False, "first": True,
                "reason": "First build: the whole team works on it."}
    else:
        await broadcast(pid, {"type": "orchestrating"})
        try:
            team = [a for a in agents if a["id"] in set(pool) | {orch.BRIEF} | orch.ON_REQUEST]
            plan = orch.decide(await run_orchestrator(pid, request, team, meta), pool, order)
        except asyncio.CancelledError:
            await broadcast(pid, {"type": "run_stopped", "orchestrator": True})
            return
        except OrchestratorUnavailable as e:
            await broadcast(pid, {"type": "error", "text": str(e), "orchestrator": True})
            return
        except Exception as e:   # never block the user: fall back to the whole team
            log.exception("Orchestrator crashed on project %s", pid)
            plan = orch.decide(None, pool, order)
    asks_contact = plan["agents"] and not plan.get("confirm") and contact.missing(plan["agents"], load_meta(pid))
    if not asks_contact:                      # otherwise start_plan shows the plan together with its questions
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
    if plan["agents"] and not plan.get("confirm"):
        await start_plan(pid, request, plan, images)


async def start_plan(pid: str, request: str, plan: dict, images: list[str]):
    """Run a decided plan, unless its agents need contact details the project doesn't have: then ask for them first
    (the answers start this same plan, see handle_answer)."""
    meta = load_meta(pid)
    keys = contact.missing(plan["agents"], meta)
    if not keys:
        schedule = None
        if plan.get("first") and not plan.get("parallel") and orch.could_pair(plan["agents"]):
            schedule = asyncio.create_task(schedule_first_build(pid, request, plan["agents"]))
        await run_chain(pid, request, plan["agents"], images=images, plan=plan, schedule=schedule)
        return
    qs = contact.questions(keys)
    reply = ("Before the team starts, a few contact details. Skip any you don't have yet: "
             "its button stays hidden until you add it.")
    await broadcast(pid, {"type": "plan", **plan, "confirm": False, "questions": qs, "needs": keys, "reply": reply})
    meta = load_meta(pid)
    meta["pending"] = {"request": request, "plan": {**plan, "confirm": False}, "images": images}
    meta["last_exchange"] = {"request": request[:PROMPT_LIMIT], "reply": reply, "questions": qs, "images": images, "needs": keys}
    save_meta(meta)


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
