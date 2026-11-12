"""The studio's WebSocket (/ws/<id>): live events down, prompts, answers and run commands up."""

import asyncio
import contextlib
import json
import secrets

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from core import limits
from core import storage
from accounts import auth
from accounts import collab
from accounts import keys
from crew import orchestrator as orch
from crew.team_files import default_ids
from crew.team_files import load_agents
from site_tools import contact

from state import (PROJECT_GONE, broadcast, human_size, is_busy, live, load_meta, log, owner_of, project_agents,
                   run_summary, save_meta, store, work_dir)
from web import _host_ok, _origin_ok, can_access, explain_exception
from runs.prompts import PROMPT_LIMIT, clean_text
from runs.pipeline import guarded_run, key_problem, run_chain, run_retry
from runs.orchestration import run_prompt, start_plan

router = APIRouter()


WS_MAX_MESSAGE = 256 * 1024      # a prompt is at most PROMPT_LIMIT characters: nothing the studio sends comes close


UNREADABLE = "The server couldn't read that message."
RUN_ACTIONS = ("prompt", "continue", "restart", "retry")


async def _ws_read(ws: WebSocket) -> tuple[dict | None, str]:
    """The next message as a dict, or (None, why it can't be used). Raises WebSocketDisconnect when the tab closes."""
    message = await ws.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    text = message.get("text")
    if text is None:
        return None, "Send messages as JSON text, like {\"type\": \"prompt\", \"text\": \"…\"}."
    if len(text) > WS_MAX_MESSAGE:
        return None, f"That message is {human_size(len(text.encode()))}; the limit is {human_size(WS_MAX_MESSAGE)}."
    try:
        msg = json.loads(text)
    except ValueError:
        return None, UNREADABLE
    if not isinstance(msg, dict):
        return None, UNREADABLE
    return msg, ""


def _ws_error_text(e: Exception, where: str) -> str:
    """What a tab is told when handling its message failed unexpectedly (the details go to the log)."""
    ref = secrets.token_hex(3)
    status, detail, _ = explain_exception(e, ref)
    if status == 500 and not isinstance(e, OSError):
        log.exception("WebSocket message failed on %s [ref %s]", where, ref)
        return f"Something went wrong on the server (ref {ref}). Try again; if it keeps happening, tell the administrator."
    log.error("WebSocket message failed on %s [ref %s]: %s: %s", where, ref, e.__class__.__name__, keys.scrub(str(e), "")[:200])
    return detail


@router.websocket("/ws/{pid}")
async def ws_endpoint(ws: WebSocket, pid: str):
    """One studio tab. Every problem with a message is answered with {"type": "error", "text"} and the socket stays
    open; it is closed only when the tab may no longer use the project (4401 logged out, 4403 another site, 4404 gone)."""
    await ws.accept()
    st = None
    try:
        try:
            pid = load_meta(pid)["id"]
        except HTTPException:
            await ws.close(code=4404, reason="This project doesn't exist.")
            return
        if not _host_ok(ws.headers.get("host", "")) or not _origin_ok(ws.headers.get("host", ""), ws.headers.get("origin")):
            await ws.close(code=4403)
            return
        token = ws.cookies.get(auth.COOKIE)
        user = await auth.user_for_token(token)
        if not user or user["role"] == "unconfirmed":
            await ws.close(code=4401, reason="Log in first.")
            return
        if not can_access(user, load_meta(pid)):
            await ws.close(code=4404, reason="This project doesn't exist.")   # the same answer as a project that doesn't exist
            return
        st = live(pid)
        st["clients"].add(ws)
        await ws.send_json({"type": "hello", "busy": is_busy(pid)})
        while True:
            msg, problem = await _ws_read(ws)
            if msg is None:
                await ws.send_json({"type": "error", "text": problem})
                continue
            # checked again on every message: logout, role changes and a deleted project apply at once
            me_now = await auth.user_for_token(token)
            if not me_now or me_now["role"] == "unconfirmed":
                await ws.close(code=4401, reason="Log in first.")
                return
            meta_now = store.get(pid)
            if meta_now is None or not can_access(me_now, meta_now):
                await ws.send_json({"type": "error", "gone": True, "text": PROJECT_GONE})
                await ws.close(code=4404, reason="This project doesn't exist.")
                return
            try:
                kind = msg.get("type")
                if kind in (*RUN_ACTIONS, "answer", "stop") and not collab.can_edit(me_now, meta_now):
                    await ws.send_json({"type": "error", "viewer": True,
                                        "text": "You can watch this project but not run its agents. Ask its owner to make you an editor."})
                    continue
                if kind in RUN_ACTIONS and not is_busy(pid):
                    wait = limits.RUNS.wait(f"u:{user['id']}")   # every run spends real money on someone's Claude key
                    if wait:
                        await ws.send_json({"type": "error", "rate_limited": True,
                                            "text": f"You've started a lot of runs this hour. Try again in {limits.wait_text(wait)}."})
                        continue
                if kind in (*RUN_ACTIONS, "answer") and not is_busy(pid):
                    if keys.USER_MODE and not meta_now.get("owner") and me_now["role"] == "admin":
                        meta_now["owner"] = str(me_now["id"])     # an older project (admins only) becomes the admin's own:
                        save_meta(meta_now)                      # it then runs on their key, like any project
                    problem = await key_problem(meta_now, me_now)
                    if problem:                         # nothing runs without a Claude API key: the studio shows how to add one
                        await ws.send_json({"type": "needs_key", **problem})
                        continue
                if kind in (*RUN_ACTIONS, "answer"):
                    st["by"] = str(user["id"])           # who to email if the run ends while nobody watches (email_after_run)
                await handle_ws_message(ws, pid, st, msg)
            except HTTPException as e:
                await ws.send_json({"type": "error", "text": e.detail})
            except (WebSocketDisconnect, asyncio.CancelledError):
                raise
            except Exception as e:
                await ws.send_json({"type": "error", "text": _ws_error_text(e, f"project {pid}")})
    except WebSocketDisconnect:
        pass
    except Exception as e:                          # e.g. the database went away while checking the session
        text = _ws_error_text(e, f"the socket of project {pid}")
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "error", "text": text})
            await ws.close(code=1011)
    finally:
        if st is not None:
            st["clients"].discard(ws)


async def handle_answer(ws: WebSocket, pid: str, st: dict, msg: dict):
    """The user answered the Orchestrator in the chat:
      {action: "confirm"}                 start the plan it proposed (no new Orchestrator call)
      {action: "cancel"}                  drop that plan
      {action: "answers", answers: {...}} its short form, answered: the request + answers go back to the Orchestrator"""
    if is_busy(pid):
        await ws.send_json({"type": "error", "text": "Agents are already working. Stop them first."})
        return
    meta = load_meta(pid)
    action = msg.get("action")
    if action in ("confirm", "cancel"):
        pending = meta.get("pending")
        if not pending:
            await ws.send_json({"type": "error", "text": "This plan is no longer waiting. Send your request again."})
            return
        meta["pending"] = None
        if action == "cancel":
            save_meta(meta)
            await broadcast(pid, {"type": "answer", "action": "cancel"})
            return
        valid = {a["id"] for a in await project_agents(meta)}
        plan = {**pending["plan"], "confirm": False}
        plan["agents"] = [i for i in plan["agents"] if i in valid]
        if not plan["agents"]:
            save_meta(meta)
            await ws.send_json({"type": "error", "text": "None of the planned agents exist any more. Send your request again."})
            return
        meta["last_exchange"] = None
        save_meta(meta)
        await broadcast(pid, {"type": "answer", "action": "confirm"})
        st["task"] = asyncio.create_task(guarded_run(pid, "a confirmed plan", start_plan(pid, pending["request"], plan, pending.get("images") or [])))
        return
    if action != "answers" or not isinstance(msg.get("answers"), dict):
        await ws.send_json({"type": "error", "text": "The server couldn't read those answers."})
        return
    ex = meta.get("last_exchange") or {}
    if not ex.get("questions"):
        await ws.send_json({"type": "error", "text": "These questions are no longer open. Send your request again."})
        return
    if ex.get("needs"):                         # contact details asked before a run: save them, then start that run
        pending = meta.get("pending")
        if not pending:
            await ws.send_json({"type": "error", "text": "These questions are no longer open. Send your request again."})
            return
        try:
            pairs = contact.apply(meta, ex["needs"], ex["questions"], msg["answers"])
        except ValueError as e:
            await ws.send_json({"type": "error", "text": str(e)})
            return
        meta["pending"], meta["last_exchange"] = None, None
        save_meta(meta)
        await broadcast(pid, {"type": "user", "text": "Contact details", "answers": pairs, "images": []})
        st["task"] = asyncio.create_task(guarded_run(pid, "a plan after contact details", start_plan(pid, pending["request"], pending["plan"], pending.get("images") or [])))
        return
    if ex.get("blanks"):                        # a team template's [blanks], filled in
        text, pairs = orch.fill_blanks(ex["request"], ex["blanks"], ex["questions"], msg["answers"])
    else:
        text, pairs = orch.answers_text(ex["request"], ex["questions"], msg["answers"])
    text = text[:PROMPT_LIMIT]
    meta["pending"] = None
    save_meta(meta)
    await broadcast(pid, {"type": "user", "text": text, "answers": pairs, "images": []})
    st["task"] = asyncio.create_task(guarded_run(pid, "a prompt with answers", run_prompt(
        pid, text, meta.get("agents") or await default_ids(await owner_of(meta)), ex.get("images") or [])))


async def handle_ws_message(ws: WebSocket, pid: str, st: dict, msg: dict):
    """One message from the studio: prompt, answer (to the Orchestrator's form or plan), stop, continue, restart or retry."""
    kind = msg.get("type")
    if kind == "answer":
        await handle_answer(ws, pid, st, msg)
        return
    if not kind or not isinstance(kind, str):
        await ws.send_json({"type": "error", "text": "Every message needs a type, like {\"type\": \"prompt\", \"text\": \"…\"}."})
        return
    if kind not in ("prompt", "stop", "continue", "restart", "retry"):
        await ws.send_json({"type": "error", "text": f"Unknown message type “{clean_text(kind, 40)}”."})
        return
    for field in ("agents", "images"):
        if msg.get(field) is not None and not isinstance(msg.get(field), list):
            await ws.send_json({"type": "error", "text": f"“{field}” must be a list."})
            return
    if kind == "prompt" and not isinstance(msg.get("text"), str):
        await ws.send_json({"type": "error", "text": "The prompt must be text."})
        return
    if kind == "prompt" and not msg["text"].strip():
        await ws.send_json({"type": "error", "text": "Write a prompt first."})
        return
    if kind == "prompt" and len(msg["text"]) > PROMPT_LIMIT:
        await ws.send_json({"type": "error", "text": f"That prompt is {len(msg['text'])} characters; the limit is {PROMPT_LIMIT}."})
        return
    if msg.get("type") == "prompt" and msg.get("text", "").strip():
        if is_busy(pid):
            await ws.send_json({"type": "error", "text": "Agents are already working. Stop them first."})
            return
        details = load_meta(pid)
        if not details.get("description") or details.get("name") == "Untitled project":
            await ws.send_json({"type": "needs_details",
                                "text": "Give this project a name and a description before the agents start."})
            return
        text = msg["text"].strip()
        owner = await owner_of(details)
        valid = {a["id"] for a in await load_agents(owner)}
        agent_ids = [i for i in msg.get("agents") or [] if isinstance(i, str) and i in valid] or await default_ids(owner)
        images = [i for i in (msg.get("images") or [])[:6]
                  if isinstance(i, str) and i.startswith("uploads/") and (work_dir(pid) / i).is_file()
                  and storage.uploads_dir(pid).resolve() in (work_dir(pid) / i).resolve().parents]
        if details.get("agents") != agent_ids:   # the switches = the agents the Orchestrator may use
            details["agents"] = agent_ids
            save_meta(details)
        await broadcast(pid, {"type": "user", "text": text, "images": images})
        found = orch.blanks(text)
        if found:                               # a team template with blanks still in it: ask for them before anyone works
            qs = orch.blank_questions(found)
            reply = "Your prompt still has blanks from the template. Fill them in, or skip any to let the team decide."
            await broadcast(pid, {"type": "plan", "intent": "unclear", "agents": [], "notes": {}, "reason": "", "reply": reply,
                                  "fallback": False, "confirm": False, "questions": qs})
            meta = load_meta(pid)
            meta["pending"] = None
            meta["last_exchange"] = {"request": text, "reply": reply, "questions": qs, "images": images, "blanks": found}
            save_meta(meta)
            return
        st["task"] = asyncio.create_task(guarded_run(pid, "a prompt", run_prompt(pid, text, agent_ids, images)))
    elif msg.get("type") in ("continue", "restart"):
        mode = msg["type"]
        if is_busy(pid):
            await ws.send_json({"type": "error", "text": "Agents are already working."})
            return
        details = load_meta(pid)
        run = details.get("run") or {}
        summary = run_summary(run)
        if not run.get("request") or run.get("status") == "running":
            await ws.send_json({"type": "error", "text": "There is no stopped run to pick up."})
            return
        if mode == "continue" and not summary["resumable"]:
            await ws.send_json({"type": "error", "text": "This run can't be continued. Start it again instead."})
            return
        if not details.get("description") or details.get("name") == "Untitled project":
            await ws.send_json({"type": "needs_details",
                                "text": "Give this project a name and a description before the agents start."})
            return
        valid = {a["id"] for a in await project_agents(details)}
        agent_ids = [i for i in run.get("agents", []) if i in valid]
        if not agent_ids:
            await ws.send_json({"type": "error", "text": "None of this run's agents exist any more."})
            return
        await broadcast(pid, {"type": "resume", "mode": mode, "request": run["request"],
                              "from": summary["next"] if mode == "continue" else agent_ids[0]})
        st["task"] = asyncio.create_task(guarded_run(pid, f"a run ({mode})", run_chain(pid, run["request"], agent_ids,
                                                                                         resume=mode == "continue",
                                                                                         images=run.get("images", []))))
    elif msg.get("type") == "retry":
        if is_busy(pid):
            await ws.send_json({"type": "error", "text": "Agents are already working."})
            return
        details = load_meta(pid)
        agent = next((a for a in await project_agents(details) if a["id"] == msg.get("agent")), None)
        if msg.get("agent") and not agent:
            await ws.send_json({"type": "error", "text": f"There's no agent called “{clean_text(msg.get('agent'), 40)}” on this project."})
            return
        request = (details.get("run") or {}).get("request") or (details.get("history") or [None])[-1]
        if not agent or not request:
            await ws.send_json({"type": "error", "text": "Nothing to retry yet. Send a prompt first."})
            return
        note = " ".join(str(msg.get("note") or "").split())[:500]
        st["task"] = asyncio.create_task(guarded_run(pid, "a retry", run_retry(pid, agent, request, note)))
    elif msg.get("type") == "stop" and is_busy(pid):
        st["task"].cancel()
