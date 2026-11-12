"""The agent-run pipeline: one agent as a `claude -p` process (run_agent, its stream), a whole run step by step
(run_chain), a retry (run_retry), browser checks and QR codes between steps, errors explained, and the email after a run."""

import asyncio
import json
import re
import secrets
import shutil
import time
import uuid
from pathlib import Path

from fastapi import HTTPException

from core import config
from core import storage
from accounts import auth
from accounts import keys
from accounts import notify as notices
from accounts import plans
from crew import market
from crew import models as model_catalog
from crew import orchestrator as orch
from crew.team_files import load_common
from site_tools import browser_check
from site_tools import images as photo_copies
from site_tools import injection
from site_tools import qr as qr_codes

from state import (AGENT_TIMEOUT, CLAUDE, MODEL, REVIEW_ROUNDS, REVIEWER, VERS, broadcast, kill_procs, live, load_meta,
                   log, owner_of, project_agents, record_stat, run_summary, safely, save_meta, site_dir, store,
                   update_run, work_dir)
from web import explain_exception
from project_data import DEFAULT_PAGES, MAX_PAGES, _page_title
from runs.prompts import (SECURITY_RULES, WEB_RULES, build_prompt, clean_text, language_rule, parse_send_back,
                          review_feedback, run_plan_brief, send_back_brief)
from runs.safety import (DENY_FETCH, DENY_READS, _guarded_files, _undo_site_changes, injection_note, outside_project,
                         own_hosts, private_values, report_scan, scan_site, scrub_paths)


class NoClaudeKey(RuntimeError):
    """CLAUDE_KEYS=user and the project's owner has no working key: nothing may run."""


async def claude_env(meta: dict) -> dict | None:
    """The environment of this project's `claude` processes: the owner's own API key and nothing else from the server
    (CLAUDE_KEYS=user), or None to inherit the server's login (CLAUDE_KEYS=server)."""
    if not keys.USER_MODE:
        return None
    owner = await owner_of(meta)
    if not owner:
        raise NoClaudeKey("This older project has no owner, so there's no Claude API key to run it with. "
                          "Duplicate it into your own account to keep working on it.")
    key = keys.decrypt(await auth.stored_claude_key(owner["id"]))
    if not key:
        raise NoClaudeKey("The agents run on the project owner's own Claude account, and no Claude API key is saved yet. "
                          "Add one in the profile (Claude API key), then send the request again.")
    return keys.env_for(str(owner["id"]), key)


async def key_problem(meta: dict, user: dict) -> dict | None:
    """What the studio shows instead of starting a run when there's no key to run with (None: all set)."""
    try:
        await claude_env(meta)
        return None
    except NoClaudeKey as e:
        mine = meta.get("owner") == str(user["id"])
        return {"text": str(e) if not mine or not meta.get("owner") else
                "The agents run on your own Claude account. Add your Claude API key once, then send your request again.",
                "mine": mine}


def short_path(path: str, folder: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(folder))
    except ValueError:
        return Path(path).name


PAGE_FILE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}\.html$")


def sync_pages(pid: str) -> list[dict] | None:
    """Add the top-level pages the agents created to the project's page list. Returns the new list, or None."""
    meta = load_meta(pid)
    pages = list(meta.get("pages") or DEFAULT_PAGES)
    known = {p["file"] for p in pages}
    titles = {p["title"] for p in pages}
    added = False
    for f in sorted(site_dir(pid).glob("*.html")):
        if len(pages) >= MAX_PAGES:
            break
        if f.name in known or not PAGE_FILE.match(f.name) or not f.is_file():
            continue
        title, n = _page_title(f.read_bytes(), f.name), 2
        base = title
        while title in titles:
            title, n = f"{base} {n}"[:40], n + 1
        titles.add(title)
        pages.append({"title": title, "file": f.name})
        added = True
    if not added:
        return None
    meta["pages"] = pages
    save_meta(meta)
    return pages


async def run_agent(pid: str, agent: dict, common: str, request: str, notes: list[str], meta: dict,
                    images: list[str] = (), extra: str = "") -> str:
    """Run one agent as a `claude -p` process inside the project folder and stream what it does."""
    folder = site_dir(pid)
    if agent.get("scope") == "mine":            # a member's own agent, or one hired from the marketplace
        found = injection.suspicious(agent.get("role", "") + "\n" + agent.get("description", ""), "a")
        if found:
            await broadcast(pid, {"type": "security", "agent": agent["id"],
                                  "text": f"The {agent['name']} agent's instructions look like a prompt injection, so it didn't run.",
                                  "items": [injection.REASONS[f] for f in found]})
            raise RuntimeError(f"The {agent['name']} agent didn't run: its instructions look like a prompt injection. "
                               f"Fix them on its page (/agents/{agent['id']}/edit), or take it out of the team, then press Continue.")
    note = await injection_note(pid, folder, request)
    if note:
        extra = (extra + "\n\n" + note).strip()
    # images are uploads/… and checks/… in works/<pid>/, next to the site: give full paths and access to those folders
    image_paths = [str((work_dir(pid) / i).resolve()) for i in images]
    prompt = build_prompt(meta, request, notes, image_paths, extra.replace("](checks/", f"]({work_dir(pid).resolve()}/checks/")
                                                                 .replace("- checks/", f"- {work_dir(pid).resolve()}/checks/"))
    extra_dirs = [d for d in (storage.uploads_dir(pid), storage.checks_dir(pid)) if images and d.exists()]
    web = agent.get("tools") == "web"
    language = await language_rule(meta)
    tools = ["Read", "Write", "Edit", "Glob", "Grep"] + (["WebSearch", "WebFetch"] if web else [])
    cmd = [
        CLAUDE, "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        # Isolation: no code-running tools, user/project settings and MCP servers ignored, file tools confined
        # to the project folder (the working directory), only these tools loaded. Web tools only for `tools: web`.
        "--restricted", "--strict-mcp-config", "--disable-slash-commands",
        "--tools", ",".join(tools),
        "--allowedTools", *tools,
        "--disallowedTools", "Bash", "Task", *(DENY_FETCH if web else ["WebFetch", "WebSearch"]), *DENY_READS,
        "--permission-mode", "acceptEdits",
        *[arg for d in extra_dirs for arg in ("--add-dir", str(d.resolve()))],
        # Prompt caching: each agent's system prompt is byte-identical for every project and run (no project data
        # in it), and this flag moves per-machine parts (cwd, env, git status) out of it, so its cache is reused
        # across projects. The server's rules stay last so they override everything before them.
        "--exclude-dynamic-system-prompt-sections",
        "--append-system-prompt", language + common + "\n\n" + agent["role"] + "\n\n" + SECURITY_RULES + ("\n\n" + WEB_RULES if web else ""),
    ]
    model = agent.get("model") or MODEL
    if agent.get("model") and not model_catalog.allowed(agent["model"]):
        raise RuntimeError(f"The {agent['name']} agent's model “{agent['model']}” isn't a known model. "
                           f"Pick one on its page (/agents/{agent['id']}/edit), then press Continue.")
    if model:
        cmd += ["--model", model]

    st = live(pid)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        env = await claude_env(meta)
    except NoClaudeKey as e:
        raise RuntimeError(str(e))
    guard = _guarded_files(folder) if web else None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=folder, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )
    except FileNotFoundError:
        raise RuntimeError(f"Claude Code wasn't found (tried “{CLAUDE}”). Install it, or set CLAUDE_BIN to its path, then restart the server.")
    except PermissionError:
        raise RuntimeError(f"Claude Code at “{CLAUDE}” isn't executable. Check its permissions or set CLAUDE_BIN.")
    st["procs"].add(proc)
    final_text = ""
    pending_edits = {}  # tool_use_id -> file
    started = time.time()
    info = {"model": model or ""}
    outcome = "error"
    stderr_task = asyncio.create_task(proc.stderr.read())   # drain stderr so a chatty process can't stall
    try:
        final_text = await asyncio.wait_for(_stream_agent(pid, agent, proc, folder, pending_edits, info, stderr_task),
                                            timeout=AGENT_TIMEOUT)
        outcome = "done"
        return final_text
    except asyncio.TimeoutError:
        limit = f"{AGENT_TIMEOUT // 60} minutes" if AGENT_TIMEOUT >= 120 else f"{AGENT_TIMEOUT} seconds"
        raise RuntimeError(f"The {agent['name']} agent ran for more than {limit} and was stopped. "
                           "Try again, or raise AGENT_TIMEOUT.")
    except asyncio.CancelledError:
        outcome = "stopped"
        raise
    finally:
        if proc.returncode is None:
            proc.kill()
        st["procs"].discard(proc)
        stderr_task.cancel()
        if guard is not None:
            undone = safely("Undoing a web agent's changes", _undo_site_changes, folder, guard) or []
            if undone:
                log.warning("Web agent %s changed site files on %s; undone: %s", agent["id"], pid, undone)
                info["undone"] = undone
                try:
                    await broadcast(pid, {"type": "security", "agent": agent["id"],
                                          "text": f"The {agent['name']} agent changed files it isn't allowed to touch "
                                                  "(it may only write research.md and plan.md). Those changes were undone.",
                                          "items": undone[:8]})
                    for rel in undone:
                        await broadcast(pid, {"type": "file_changed", "agent": agent["id"], "file": rel.rsplit(" (", 1)[0]})
                except Exception:
                    log.exception("Couldn't report undone changes")
        if agent["id"] == REVIEWER and outcome == "done" and market.critic_score(final_text) is not None:
            info["score"] = market.critic_score(final_text)
        store.index_site(pid)                     # every file the agent left, with its uuid
        safely("Recording stats", record_stat, pid, {"agent": agent["id"], "status": outcome, "ts": started,
                                                     "run": (meta.get("run") or {}).get("started"),
                                                     "request": request[:100], "seconds": round(time.time() - started, 1), **info})


def run_error_text(e: BaseException, where: str) -> str:
    """What the studio shows when a run, a retry or a plan fails. Our own explained failures (RuntimeError, HTTPException)
    are shown as they are, without keys or server paths; anything else is logged with a ref and explained."""
    if isinstance(e, HTTPException):
        return str(e.detail)
    if isinstance(e, RuntimeError) and str(e):
        return scrub_paths(keys.scrub(str(e), ""))
    ref = secrets.token_hex(3)
    status, detail, _ = explain_exception(e, ref)
    if status == 500 and not isinstance(e, OSError):
        log.exception("%s failed [ref %s]", where[:1].upper() + where[1:], ref)
        return (f"The run stopped because of a problem on the server (ref {ref}). Press Continue to try again; "
                "if it keeps happening, tell the administrator.")
    log.error("%s failed [ref %s]: %s: %s", where[:1].upper() + where[1:], ref, e.__class__.__name__,
              scrub_paths(keys.scrub(str(e), ""))[:200])
    return detail + (" Press Continue when it's back." if status == 503 else "")


async def guarded_run(pid: str, what: str, coro):
    """Run a project's background job (a prompt, a run, a retry, a plan). Whatever escapes it is logged with its context
    and shown in the studio, and a run left "running" is marked as failed: nothing dies silently or keeps the studio waiting."""
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception as e:
        text = run_error_text(e, f"{what} on project {pid}")
        try:
            run = None
            if ((store.get(pid) or {}).get("run") or {}).get("status") == "running":
                run = update_run(pid, status="error", running=[], current=None, error=text[-500:])
            await broadcast(pid, {"type": "error", "text": text, **({"run": run_summary(run)} if run else {})})
        except Exception:
            log.exception("Couldn't report the failure of %s on project %s", what, pid)


def explain_failure(text: str) -> str:
    """Turn Claude Code's raw error output into something actionable."""
    text = scrub_paths(keys.scrub(text, ""))         # never show an API key or a server path, even one echoed in an error
    low = text.lower()
    if keys.USER_MODE and keys.explain(text):
        return keys.explain(text)
    if "not logged in" in low or "/login" in low or "invalid api key" in low or "authentication" in low:
        return "Claude Code isn't logged in. Run `claude` in a terminal, log in, then try again."
    if "usage limit" in low or "rate limit" in low or "429" in low or "overloaded" in low:
        return "Claude is rate-limited or overloaded right now. Wait a little, then press Continue."
    if "unrecognized_model" in low or ("model" in low and ("not found" in low or "invalid" in low or "not available" in low)):
        return f"The model for this agent isn't available: {text.strip()[-200:]}. Change it on the Agents page."
    return text.strip()[-500:] or "The agent stopped unexpectedly."


async def _stream_agent(pid: str, agent: dict, proc, folder: Path, pending_edits: dict, info: dict, stderr_task) -> str:
    st = live(pid)
    final_text = ""
    async for raw in proc.stdout:
        try:
            ev = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(ev, dict):
            continue
        try:
            final_text = await _agent_event(pid, agent, ev, folder, pending_edits, info, final_text)
        except (AttributeError, KeyError, TypeError) as e:     # a line in a shape we don't know: skip it, keep the run going
            log.warning("Skipped an unexpected line from the %s agent on %s: %s", agent["id"], pid, e)
        if info.get("escape"):
            await _stop_escape(pid, agent, proc, info["escape"], info.get("escape_why"))

    await proc.wait()
    if proc.returncode != 0:
        err = (await stderr_task).decode(errors="replace")
        raise RuntimeError(explain_failure(err or f"Claude Code exited with code {proc.returncode}."))
    return final_text


async def _stop_escape(pid: str, agent: dict, proc, target: str, fetch_why: str | None = None):
    """An agent reached outside its project, or fetched a private address or one that carries data out:
    stop it now, say so in the chat, and fail the step."""
    if proc.returncode is None:
        proc.kill()
    log.warning("Agent %s on %s stopped: %s (%s)", agent["id"], pid, fetch_why or "outside its project", target)
    if fetch_why:
        await broadcast(pid, {"type": "security", "agent": agent["id"],
                              "text": f"The {agent['name']} agent tried to fetch {fetch_why}, so it was stopped.",
                              "items": [target]})
        raise RuntimeError(f"The {agent['name']} agent was stopped: it tried to fetch {fetch_why}. "
                           "Check your request and the project files for instructions that ask for it, then press Continue.")
    await broadcast(pid, {"type": "security", "agent": agent["id"],
                          "text": f"The {agent['name']} agent tried to open a file outside this project, so it was stopped.",
                          "items": [target]})
    raise RuntimeError(f"The {agent['name']} agent was stopped: it tried to open a file outside this project. "
                       "Check your request and the project files for instructions that ask for other files, then press Continue.")


async def _agent_event(pid: str, agent: dict, ev: dict, folder: Path, pending_edits: dict, info: dict, final_text: str) -> str:
    """One line of an agent's stream-json output: broadcast what it did. Returns the latest text of its reply."""
    kind = ev.get("type")
    if kind == "system" and ev.get("model"):
        info["model"] = ev["model"]
    elif kind == "assistant":
        for block in ev["message"].get("content", []):
            if block.get("type") == "text" and block["text"].strip():
                final_text = block["text"].strip()
                await broadcast(pid, {"type": "agent_text", "agent": agent["id"], "text": final_text})
            elif block.get("type") == "tool_use":
                inp = block.get("input", {})
                escape = outside_project(pid, folder, block.get("name"), inp)
                if escape:                          # stopped by _stream_agent before it goes any further
                    info["escape"] = f"{block.get('name')}: {clean_text(scrub_paths(escape), 160)}"
                    return final_text
                if block.get("name") == "WebFetch":
                    why = injection.unsafe_fetch(inp.get("url", ""), own_hosts(), private_values(pid))
                    if why:
                        info["escape"] = f"WebFetch: {clean_text(str(inp.get('url', '')), 160)}"
                        info["escape_why"] = why
                        return final_text
                if inp.get("file_path"):
                    target = short_path(inp["file_path"], folder)
                else:   # Glob/Grep pattern, WebSearch query, WebFetch url
                    target = clean_text(inp.get("pattern") or inp.get("path") or inp.get("query") or inp.get("url") or "", 160)
                if block["name"] in ("Write", "Edit"):
                    pending_edits[block["id"]] = target
                await broadcast(pid, {"type": "tool", "agent": agent["id"],
                                      "tool": block["name"], "target": target})

    elif kind == "user":
        for block in ev["message"].get("content", []):
            if block.get("type") == "tool_result" and block.get("tool_use_id") in pending_edits:
                await broadcast(pid, {"type": "file_changed", "agent": agent["id"],
                                      "file": pending_edits.pop(block["tool_use_id"])})

    elif kind == "result":
        usage = ev.get("usage") or {}
        denied = [{"tool": d.get("tool_name"), "target": clean_text((d.get("tool_input") or {}).get("file_path")
                   or (d.get("tool_input") or {}).get("path") or (d.get("tool_input") or {}).get("pattern") or "", 160)}
                  for d in (ev.get("permission_denials") or [])]
        if denied:
            info["denied"] = denied
            await broadcast(pid, {"type": "security", "agent": agent["id"],
                                  "text": f"The {agent['name']} agent tried something it isn't allowed to do, and it was blocked.",
                                  "items": [f"{d['tool']}: {d['target']}" for d in denied[:8]]})
        info.update(cost=round(ev.get("total_cost_usd") or 0, 5), turns=ev.get("num_turns"),
                    api_seconds=round((ev.get("duration_api_ms") or 0) / 1000, 1),
                    tokens_in=(usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
                              + (usage.get("cache_read_input_tokens") or 0),
                    tokens_out=usage.get("output_tokens") or 0)
        if ev.get("result"):
            final_text = ev["result"].strip()
        if ev.get("is_error"):
            raise RuntimeError(explain_failure(final_text or "The agent reported an error."))
    return final_text


# The agents that get a real browser's view of the site first (see browser_check.py).
BROWSER_CHECK_AGENTS = {"responsive", "a11y", "qa", "critic"}   # layout problems for Responsive, axe results for Accessibility


async def safe_browser_check(pid: str) -> dict | None:
    """A failed or slow check never stops the run: the agent then works from the code alone."""
    meta = load_meta(pid)
    pages = [p["file"] for p in meta.get("pages") or []] or ["index.html"]
    try:
        check = str(uuid.uuid4())                 # works/<pid>/checks/<check>/: only the latest check is kept
        shutil.rmtree(storage.checks_dir(pid), ignore_errors=True)
        report = await asyncio.wait_for(browser_check.check_site(site_dir(pid), pages, storage.checks_dir(pid) / check, f"checks/{check}"), 90)
        store.index_files(pid, "check", storage.checks_dir(pid))
        return report
    except Exception as e:
        log.warning("Browser check failed for %s: %s", pid, e)
        return None


async def run_chain(pid: str, request: str, agent_ids: list[str], resume: bool = False, images: list[str] = (),
                    plan: dict | None = None, schedule: asyncio.Task | None = None):
    """Run the team in order, one step at a time: one agent, or a group the Orchestrator lets work at the same time
    (plan["parallel"], see orchestrator.next_step). With resume=True, skip the agents that already finished in the saved
    run and give the rest the notes those agents left. `plan` is the Orchestrator's decision (reason and notes per agent).
    `schedule` (a first build) is the Orchestrator still choosing those groups: they apply from the step after it answers."""
    st = live(pid)
    meta = load_meta(pid)
    if await asyncio.to_thread(photo_copies.ensure, meta, site_dir(pid)):      # photos added before the WebP copies existed
        save_meta(meta, touch=False)
        store.index_site(pid)
    team = [a for a in await project_agents(meta) if a["id"] in agent_ids]  # snapshot of the owner's agents at run start
    owner = await owner_of(meta)
    tier = plans.of(owner)                                  # the owner's plan (`plan` is the Orchestrator's)
    no_web = [a for a in team if a.get("tools") == "web" and not tier["research"]]
    team = [a for a in team if a not in no_web]           # web research is a Pro feature
    checked = BROWSER_CHECK_AGENTS if tier["browser_checks"] == "all" else {REVIEWER}
    limit, spent = (plans.run_limit(owner) if keys.USER_MODE else 0), 0.0
    st["keep_versions"] = tier["versions"]
    common = load_common()
    prev = meta.get("run") or {}
    done = [i for i in prev.get("done", []) if i in agent_ids] if resume else []
    notes = list(prev.get("notes", [])) if resume else []
    images = list(images) or (list(prev.get("images", [])) if resume else [])
    review_round = prev.get("review_round", 0) if resume else 0
    send_back = dict(prev.get("send_back") or {}) if resume else {}
    plan = plan if plan is not None else (prev.get("plan") or {}) if resume else {}
    meta["run"] = {"request": request, "images": images, "agents": [a["id"] for a in team], "done": done, "notes": notes,
                   "review_round": review_round, "send_back": send_back, "plan": plan,
                   "current": None, "running": [], "status": "running", "error": "",
                   "started": prev.get("started", time.time()) if resume else time.time(), "updated": time.time()}
    save_meta(meta)

    await broadcast(pid, {"type": "run_start", "request": request, "name": meta["name"],
                          "agents": meta["run"]["agents"], "pool": meta.get("agents") or [], "done": done, "resumed": resume})
    if no_web:
        await broadcast(pid, {"type": "notice", "upgrade": "pro",
                              "text": f"{', '.join(a['name'] for a in no_web)} skipped: agents that search the web come with the Pro plan."})
    seen_findings = set(safely("Scan", scan_site, site_dir(pid)) or [])
    if safely("Snapshot", VERS.snapshot, pid, f"Before: {request[:60]}", kind="before", request=request):
        await broadcast(pid, {"type": "versions"})
    try:
        while True:
            if schedule is not None and schedule.done() and not plan.get("parallel"):
                groups = schedule.result() if not schedule.cancelled() and schedule.exception() is None else []
                schedule = None
                if groups:                          # the Orchestrator's groups for a first build arrived while Brief worked
                    plan = {**plan, "parallel": groups}
                    update_run(pid, plan=plan)
                    await broadcast(pid, {"type": "schedule", "groups": groups})
            remaining = [a["id"] for a in team if a["id"] not in done]
            step = [a for i in orch.next_step(remaining, plan.get("parallel") or []) for a in team if a["id"] == i]
            if not step:
                break
            extras, fixes = {}, {}
            for agent in step:
                fix = fixes[agent["id"]] = send_back.get(agent["id"])
                if agent["id"] == REVIEWER:
                    extras[agent["id"]] = send_back_brief(team, done, review_round)
                else:
                    extras[agent["id"]] = review_feedback(fix, review_round) if fix else run_plan_brief(plan, agent["id"])
            update_run(pid, current=step[0]["id"], running=[a["id"] for a in step])
            agent_images = {a["id"]: images for a in step}
            looked = [a for a in step if a["id"] in checked]
            if looked and browser_check.available():  # one check serves every agent of the step that gets one
                await broadcast(pid, {"type": "browser_check_start", "agent": looked[0]["id"]})
                report = await safe_browser_check(pid)
                if report is not None:
                    for a in looked:
                        extras[a["id"]] += browser_check.prompt_block(report)
                        agent_images[a["id"]] = [*images, *report["screenshots"]]
                    await broadcast(pid, {"type": "browser_check", "agent": looked[0]["id"], "text": browser_check.summary(report),
                                          "problems": browser_check.problems(report), "screenshots": report["screenshots"]})
            if len(step) > 1:
                await broadcast(pid, {"type": "parallel", "agents": [a["id"] for a in step]})
            for agent in step:
                await broadcast(pid, {"type": "agent_start", "agent": agent["id"], **({"fix": True} if fixes[agent["id"]] else {}),
                                      **({"with": [a["id"] for a in step if a is not agent]} if len(step) > 1 else {})})
            # every agent of the step reads the notes left before it, then they work at the same time
            meta_now = load_meta(pid)
            results = await asyncio.gather(*(run_agent(pid, a, common, request, notes, meta_now, agent_images[a["id"]], extras[a["id"]])
                                             for a in step), return_exceptions=True)
            failure = next((r for r in results if isinstance(r, BaseException)), None)
            finished, items = [], []
            for agent, summary in zip(step, results):
                if isinstance(summary, BaseException):
                    continue
                fix = fixes[agent["id"]]
                summary += await build_qr_codes(pid)
                if agent["id"] == REVIEWER:
                    allowed = [] if review_round >= REVIEW_ROUNDS else [a["id"] for a in team if a["id"] in done and a["id"] != REVIEWER]
                    items, summary = parse_send_back(summary, allowed)
                    summary = summary or "Review done."
                notes.append(f"- {agent['name']} agent{' (fix)' if fix else ''}: {summary}")
                done.append(agent["id"])
                send_back.pop(agent["id"], None)
                finished.append((agent, summary, fix))
            v = None
            if finished:
                names = " + ".join(f"{a['name']}{' (fix)' if f else ''}" for a, _, f in finished)
                v = safely("Snapshot", VERS.snapshot, pid, f"After {names}", kind="agent", agent=finished[0][0]["id"], request=request)
            for agent, summary, _fix in finished:
                await broadcast(pid, {"type": "agent_done", "agent": agent["id"], "text": summary,
                                      "stat": last_stat(pid, agent["id"]), "version": v["n"] if v else None})
            if finished:
                await report_scan(pid, {"id": finished[0][0]["id"], "name": " + ".join(a["name"] for a, _, _ in finished)}, seen_findings)
                new_pages = safely("Pages", sync_pages, pid)
                if new_pages:
                    await broadcast(pid, {"type": "pages", "pages": new_pages})
            if items:
                # Send the work back: those agents (and the Critic after them) run again, in team order.
                review_round += 1
                send_back = {i["agent"]: i for i in items}
                done = [d for d in done if d not in send_back and d != REVIEWER]
                await broadcast(pid, {"type": "send_back", "agent": REVIEWER, "round": review_round,
                                      "rounds": REVIEW_ROUNDS, "items": items})
            update_run(pid, done=done, notes=notes, current=None, running=[], review_round=review_round, send_back=send_back)
            if failure is not None:
                if isinstance(failure, asyncio.CancelledError):
                    raise failure
                update_run(pid, current=next(a["id"] for a, r in zip(step, results) if r is failure))
                raise failure
            spent += sum(float(last_stat(pid, a["id"]).get("cost") or 0) for a, _, _ in finished)
            if limit and spent >= limit and any(a["id"] not in done for a in team):
                # the member's own money: stop at their limit, keep what's done, and let them Continue on purpose
                run = update_run(pid, status="stopped", error="")
                await broadcast(pid, {"type": "limit", "spent": round(spent, 2), "limit": limit,
                                      "text": f"This run reached your ${limit:g} spending limit (about ${spent:.2f} on your Claude account). "
                                              "Press Continue to keep going, or change the limit in your profile."})
                await broadcast(pid, {"type": "run_stopped", "run": run_summary(run)})
                await email_after_run(pid, False, f"it reached the ${limit:g} spending limit per run.")
                return
        meta = load_meta(pid)
        meta["history"].append(request)
        meta["run"].update(status="done", current=None, running=[], updated=time.time())
        save_meta(meta)
        await broadcast(pid, {"type": "run_done"})
        await email_after_run(pid, True)
    except asyncio.CancelledError:
        kill_procs(st)
        run = safely("Saving the stopped run", update_run, pid, status="stopped", running=[])
        await broadcast(pid, {"type": "run_stopped", "run": run_summary(run or {})})
    except Exception as e:
        text = run_error_text(e, f"a run on project {pid}")
        run = safely("Saving the failed run", update_run, pid, status="error", running=[], error=text[-500:])
        await broadcast(pid, {"type": "error", "text": text, "run": run_summary(run or {})})
        await email_after_run(pid, False, text)
    finally:
        kill_procs(st)
        if schedule is not None and not schedule.done():
            schedule.cancel()


async def email_after_run(pid: str, ok: bool, why: str = ""):
    """Builds take minutes and people leave the tab: email whoever started a long run, unless a studio tab is open."""
    try:
        st = live(pid)
        run = (store.get(pid) or {}).get("run") or {}
        seconds = time.time() - float(run.get("started") or time.time())
        if seconds < config.RUN_EMAIL_AFTER or st["clients"]:
            return
        who = st.get("by") or (store.get(pid) or {}).get("owner")
        if who:
            await notices.run_finished(pid, who, ok, seconds, keys.scrub(why, "") if why else "")
    except Exception:
        log.exception("Couldn't email the end of a run on %s", pid)


async def build_qr_codes(pid: str) -> str:
    """Draw the QR codes an agent listed in qr.json (see qr.py). Returns a line for its summary, or ""."""
    made, problems = await asyncio.to_thread(qr_codes.build, site_dir(pid))
    if made:
        store.index_site(pid)
    text = (f" The server made {len(made)} QR code{'s' if len(made) != 1 else ''}: {', '.join(made)}." if made else "")
    return text + (f" Skipped: {'; '.join(problems)}." if problems else "")


def last_stat(pid: str, agent_id: str | None = None) -> dict:
    """The latest agent run recorded on this project (of one agent: several may finish close together)."""
    try:
        stats = load_meta(pid).get("stats") or []
    except HTTPException:
        return {}
    return next((e for e in reversed(stats) if agent_id is None or e.get("agent") == agent_id), {})


async def run_retry(pid: str, agent: dict, request: str, note: str):
    """Run a single agent again on the last request, with an optional extra instruction.
    The saved run state (for Continue / Start again) is left untouched."""
    st = live(pid)
    meta = load_meta(pid)
    run = meta.get("run") or {}
    notes = [n for n in run.get("notes", []) if not n.startswith(f"- {agent['name']} agent:")]
    full = request + (f"\n\nRetry — the user asked the {agent['name']} agent to redo its step with this instruction: {note}"
                      if note else f"\n\nRetry — the user asked the {agent['name']} agent to redo its step and do it better.")
    await broadcast(pid, {"type": "run_start", "request": request, "name": meta["name"], "agents": [agent["id"]],
                          "retry": True, "note": note})
    safely("Snapshot", VERS.snapshot, pid, f"Before retrying {agent['name']}", kind="before", request=request)
    found_before = safely("Scan", scan_site, site_dir(pid)) or []
    try:
        await broadcast(pid, {"type": "agent_start", "agent": agent["id"], "retry": True})
        summary = await run_agent(pid, agent, load_common(), full, notes, meta, run.get("images", []),
                                  send_back_brief([], [], REVIEW_ROUNDS) if agent["id"] == REVIEWER else "")
        summary += await build_qr_codes(pid)
        if agent["id"] == REVIEWER:
            summary = parse_send_back(summary, [])[1] or "Review done."
        v = safely("Snapshot", VERS.snapshot, pid, f"After {agent['name']} (retry)", kind="agent", agent=agent["id"], request=request)
        await report_scan(pid, agent, set(found_before))
        await broadcast(pid, {"type": "agent_done", "agent": agent["id"], "text": summary, "stat": last_stat(pid, agent["id"]),
                              "version": v["n"] if v else None})
        await broadcast(pid, {"type": "run_done", "retry": True})
    except asyncio.CancelledError:
        kill_procs(st)
        await broadcast(pid, {"type": "run_stopped", "retry": True, "run": run_summary(load_meta(pid).get("run"))})
    except Exception as e:
        await broadcast(pid, {"type": "error", "text": run_error_text(e, f"a retry on project {pid}"), "retry": True})
    finally:
        kill_procs(st)
