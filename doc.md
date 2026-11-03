# Agents Atelier — technical documentation

This document explains how Agents Atelier works inside: its architecture, the agent run pipeline, what is stored on
disk, the HTTP and WebSocket APIs, the agent file format, versioning, stats and error handling.
For what the app does and how to run it, see [README.md](README.md).

---

## 1. Architecture

```
 Browser (vanilla HTML/CSS/JS, no build step)
   ├─ HTTP  /api/…   JSON API: projects, files, versions, stats, agents, presets
   ├─ WS    /ws/<id> live events for one project; prompts and run commands go up the same socket
   └─ HTTP  /work/<id>/…   the generated site, served as static files (live preview iframe)
        │
 FastAPI server (server/main.py, one process, asyncio)
   ├─ per project: at most one running task (a run or a retry)
   ├─ per agent:   one `claude -p` subprocess, its JSON stream parsed line by line
   └─ storage:     plain files under work/ and agents/ (no database)
        │
 Claude Code CLI (`claude`), using the user's own login and plan
```

| Part              | Technology                                                        |
|-------------------|-------------------------------------------------------------------|
| Server            | Python 3.10+, FastAPI, Uvicorn (`uvicorn[standard]` for WebSockets) |
| Agents            | Claude Code CLI in print mode (`claude -p … --output-format stream-json`) |
| Front end         | Static HTML pages with inline CSS and JS, plus shared `base.css`, `site.css`, `ui.js`, `icons.js` |
| Storage           | JSON and JSONL files, project folders, Markdown agent files       |
| Generated sites   | Plain HTML, CSS and JS, which the agents' shared rules enforce    |

### Source files

```
server/
├── main.py       FastAPI app: pages, JSON API, WebSocket, run pipeline, stats, error handling
├── agents.py     agents/ loader and editor (frontmatter parser, validation, ordering, presets)
├── versions.py   code snapshots: create, list, diff, restore, prune
└── static/
    ├── landing.html  /            home page
    ├── projects.html /projects    projects list (server-side pagination)
    ├── studio.html   /p/<uuid>    the studio: chat, preview, code editor, team, versions, stats
    ├── agents.html   /agents      agent editor
    ├── guide.html    /guide       user guide
    ├── base.css      design tokens + shared components (buttons, inputs, dialogs, toasts, error states)
    ├── site.css      layout for content pages (header, nav, page)
    ├── teams.html    /teams…      team pages
    ├── ui.js         api(), toast(), dialogs, showError(), fmtSize(), theme (setTheme/cycleTheme),
    │                 command palette (openPalette, registerCommands), importFlow()
    ├── icons.js      hand-drawn SVG line icons: icon(name) and <i data-icon="name">
    └── logo.svg      logo and favicon
```

---

## 2. Data on disk

Nothing is stored in a database. Everything is a file, so it can be inspected, backed up or edited by hand.

```
work/
├── <uuid>/                        the generated site for one project (served at /work/<uuid>/)
│   ├── index.html, style.css, script.js, plan.md, assets/…
│   └── uploads/                   reference images attached to prompts (not versioned)
├── .projects/
│   ├── <uuid>.json                project metadata (see below)
│   └── <uuid>.events.jsonl        the chat log: one event per line
└── .versions/
    └── <uuid>/
        ├── index.json             list of versions (oldest first)
        └── 0001/, 0002/, …        full copies of the site at each version
agents/
├── _common.md                     shared rules given to every agent
├── 01-brief.md … 15-critic.md     one file per agent; the number sets the order
└── _presets.json                  presets saved by the user (built-in presets live in code)
```

### Project metadata — `work/.projects/<uuid>.json`

```jsonc
{
  "id": "7d49ead2-…",               // UUID v4, also the folder name
  "name": "Bean There coffee",      // required, ≤ 80 chars
  "description": "A cozy café…",    // required, ≤ 300 chars; sent to every agent
  "created": 1793721789.07,         // unix seconds
  "updated": 1793721792.00,         // bumped on every save (sorting, "last activity")
  "history": ["a landing page…"],   // requests whose run finished (last 6 go into the prompt)
  "agents": ["ux", "design", …],    // the team switched on for this project
  "pages": [{"title": "Home", "file": "index.html"}, {"title": "About", "file": "about.html"}],   // first = index.html
  "run": {                          // the latest run, kept for Continue / Start again
    "request": "…", "images": ["uploads/x.png"],
    "agents": ["ux", "html"], "done": ["ux"], "notes": ["- UX agent: …"],
    "current": "html",
    "status": "running | stopped | error | interrupted | done",
    "error": "", "started": 1793…, "updated": 1793…
  },
  "stats": [ { …one entry per agent run, see §7… } ]   // last 500
}
```

Every write goes through `write_json()`: it writes to `<file>.tmp`, then `os.replace()`. A crash mid-write never
leaves a half-written file behind.

### Chat log — `work/.projects/<uuid>.events.jsonl`

Each event broadcast to the studio is appended as one JSON line, with a sequence number `seq` and a timestamp `ts`.
`file_changed` events are not saved; they reuse the last `seq`. Opening a project replays this log to rebuild the chat.
Damaged lines are skipped.

---

## 3. Agents

### File format

```markdown
---
id: html                      # unique, [a-z0-9-], ≤ 32 chars; defaults to the file name without its number
name: HTML                    # label in the UI, ≤ 40 chars
icon: code                    # an icon name from server/static/icons.js
color: "#f97316"              # accent colour, #rrggbb
default: true                 # switched on for new projects
description: Builds index.html — …   # one line for the UI, ≤ 160 chars
model: sonnet                 # optional: --model passed to Claude Code (alias or full id)
---
You are the HTML agent. …     # the role prompt (everything after the frontmatter)
```

- The parser (`agents._parse`) is a small `key: value` reader, not full YAML. A ` #` starts a comment, which is
  why descriptions can't contain ` #`.
- `model` must be empty or in the model list (`server/models.py`). The list is built from:
  - the aliases in `claude --help` ("an alias for the latest model (e.g. 'fable', 'opus', or 'sonnet')")
  - the predefined full names (`KNOWN_FULL`)
  - custom names checked with `POST /api/models/check`, which runs `claude -p … --model <name> --output-format json`,
    treats `unrecognized_model` or an empty `modelUsage` as invalid, and records the resolved model id
  Saving an agent with a model outside the list returns `400`. A file edited by hand with an unknown model is flagged
  (`model_ok: false`), and that agent fails at once instead of mid-run.
- `load_agents()` re-reads the folder on every call, so edits apply to the next run without a restart. A run takes a
  snapshot of the team when it starts.
- Files whose names start with `_` or `.` aren't agents.
- Edits made through the API rewrite **every** agent file with fresh numbers (`01-`, `02-`, …) in the requested order.
  Rendering is byte-for-byte stable, so saving an unchanged agent leaves its file identical.

### Shared rules — `agents/_common.md`

Every agent gets these rules before its own role. They define the contract between agents:

- **Files:** `index.html`, `style.css`, `script.js`, `plan.md`, `assets/`. Plain HTML/CSS/JS, no external resources
  except Google Fonts.
- **Teamwork:**
  - read before editing, do only your own job
  - make small edits rather than rewriting files
  - on a follow-up request, change only what it needs
- **`plan.md` sections:** `## Brief · ## Structure · ## Design tokens · ## Copy · ## Notes · ## Review`. Each section
  belongs to one agent, and `## Notes` carries hand-over hooks such as `data-*` attributes.
- **Reply:** one or two sentences. These become the next agents' "notes".

### Presets

The four built-in presets are computed from the current agents (`agents.builtin_presets()`): Default, Quick, Polish
and Full. User presets are stored in `agents/_presets.json` as `{id, name, agents}`, and unknown agent ids are
dropped when presets are loaded.

---

## 4. The run pipeline

### One agent = one Claude Code process

`run_agent()` starts:

```
claude -p "<prompt>"
  --output-format stream-json --verbose
  --append-system-prompt "<_common.md>\n\n<agent role>"
  --allowedTools Read Write Edit Glob Grep
  --disallowedTools Bash WebFetch WebSearch Task
  --permission-mode acceptEdits
  [--model <agent.model or AGENT_MODEL>]
```

The working directory is `work/<uuid>/`, so file tools are confined to the project folder.

The **prompt** is built from (each in its own tagged block, see §9):
- the site's pages (`<site_pages>`: one page, or the list of files with the shared header/nav/footer rule)
- the project name and description
- the last 6 finished requests
- the current request
- the notes left by earlier agents in this run ("- CSS agent: …")
- the reference images, which the agent is told to open with `Read`

The **stdout stream** is parsed line by line in `_stream_agent()`:

| Claude Code event            | What the server does                                                       |
|------------------------------|----------------------------------------------------------------------------|
| `system` (init)              | records the actual model used                                              |
| `assistant` → `text`         | broadcasts `agent_text` (the latest text becomes the agent's summary)      |
| `assistant` → `tool_use`     | broadcasts `tool` (`Read` / `Write` / `Edit` / `Glob` / `Grep` + target); remembers writes |
| `user` → `tool_result`       | for a remembered write: broadcasts `file_changed`, which triggers the preview reload |
| `result`                     | records cost, tokens, turns and API time; `is_error` raises a readable error |

The **stderr stream** is read by a separate task the whole time, so a process that writes a lot of error output
can't block. The whole agent is limited by `asyncio.wait_for(…, AGENT_TIMEOUT)`, and the process is killed on
timeout or cancellation.

### A run: `run_chain(pid, request, agent_ids, resume=False, images=())`

1. Snapshot the selected agents, in file order. Save `run` with `status: running`, broadcast `run_start`.
2. Save a version labelled **"Before: <request>"**. It's skipped if the code hasn't changed since the last version.
3. For each agent not already in `done`:
   1. set `run.current`, broadcast `agent_start`, run the agent
   2. append its note, add it to `done`, save the run state
   3. save a version **"After <Agent>"**, then broadcast `agent_done` with that version's number and the agent's stats
4. Finally: add the request to `history`, set `status: done`, broadcast `run_done`.

| Ends with          | Run status    | Event          | Offered in the UI           |
|--------------------|---------------|----------------|-----------------------------|
| all agents done    | `done`        | `run_done`     | —                           |
| user pressed stop  | `stopped`     | `run_stopped`  | Continue · Start again      |
| an agent failed    | `error`       | `error`        | Continue · Start again      |
| server restarted   | `interrupted` | (set at startup) | Continue · Start again    |

- **Continue** (`resume=True`) keeps `done` and `notes`, so only the remaining agents run. The agent that was
  interrupted runs again from the start; it reads the files it had already changed.
- **Start again** runs the whole saved team from the first agent on the same request.
- **Retry** (`run_retry()`) runs one agent on the last request, with an optional extra instruction appended. It
  saves versions before and after, and leaves `run` untouched, so a paused run can still be continued.

Only one task runs per project at a time (`is_busy()`). While it runs, file edits, downloads, restores and new
prompts are refused with `409` or an `error` event. Different projects can run at the same time.

---

## 5. HTTP API

All responses are JSON. Errors are always `{"detail": "<readable message>"}`, plus `"ref"` on a `500` (see §8).

### Pages

| Method | Path          | Returns                                                             |
|--------|---------------|---------------------------------------------------------------------|
| GET    | `/`           | landing page                                                        |
| GET    | `/projects`   | projects page                                                       |
| GET    | `/p/<uuid>`   | the studio for a project (404 page if unknown)                      |
| GET    | `/agents`, `/agents/new`, `/agents/<id>`, `/agents/<id>/edit`, `/agents/rules` | agent pages (one file, `agents.html`, routes on the path) |
| GET    | `/teams`, `/teams/new`, `/teams/<id>`, `/teams/<id>/edit` | team pages (`teams.html`)  |
| GET    | `/guide`      | guide                                                               |
| GET    | `/studio`     | redirect to `/projects` (the studio is only opened from a project)  |
| GET    | `/work/<uuid>/…` | the generated site (no-store caching), also `/work/.versions/<uuid>/<NNNN>/…` |

### Projects

| Method | Path                                   | Body / query                         | Notes                                    |
|--------|----------------------------------------|--------------------------------------|------------------------------------------|
| GET    | `/api/projects`                        | —                                    | all projects, most recent first (list)   |
| GET    | `/api/projects?page=&per_page=&q=&status=&sort=` | `status`: all · working · built · empty; `sort`: updated · created · name; `per_page` ≤ 96 | `{items, page, per_page, pages, total, counts, prompts}` |
| POST   | `/api/projects`                        | `{name, description, pages?}`        | both required; `pages` is a list of page titles (default `["Home"]`, max 12) |
| GET    | `/api/projects/<id>`                   | —                                    | metadata + `events` + `busy` + `run` summary |
| PATCH  | `/api/projects/<id>`                   | any of `{name, description, agents, pages}` |                                   |
| DELETE | `/api/projects/<id>`                   | —                                    | `409` while busy; also deletes versions  |
| GET    | `/api/projects/<id>/files`             | —                                    | `[{path, size, kind, content?, url?, too_large?}]` |
| PUT    | `/api/projects/<id>/files`             | `{path, content}`                    | text files only, ≤ 500 KB, `409` while busy |
| POST   | `/api/projects/<id>/uploads`           | `{name, data: "data:image/png;base64,…"}` | ≤ 5 MB → `{path, url, size}`        |
| GET    | `/api/projects/<id>/download`          | —                                    | `.zip` of the site, `409` while busy      |

Each project in a list has `id, name, description, created, updated, busy, paused, prompts, agents, files, size, has_preview`.

A file's `kind` is `text`, `image`, `binary` or `other`. `content` is only present for text files of 500 KB or less;
images get a `url` instead.

### Versions

| Method | Path                                             | Notes                                                    |
|--------|--------------------------------------------------|----------------------------------------------------------|
| GET    | `/api/projects/<id>/versions`                    | newest first: `{n, label, kind, agent, request, ts, files, size, hash}` |
| POST   | `/api/projects/<id>/versions`                    | `{label?}`; `400` if nothing changed, `409` while busy    |
| GET    | `/api/projects/<id>/versions/<n>/diff?against=m` | per-file unified diff, from version n to the current code (or to version m) |
| POST   | `/api/projects/<id>/versions/<n>/restore`        | saves the current code first (`backup`), then restores; broadcasts `restored` |
| GET    | `/api/projects/<id>/versions/<n>/download`       | `.zip` of that version                                    |

### Agents, presets, stats

| Method   | Path                    | Notes                                                              |
|----------|-------------------------|--------------------------------------------------------------------|
| GET      | `/api/agents?full=1`    | team in order; `full` adds `role` and `file`                       |
| POST     | `/api/agents`           | `{id, name, icon, color, default, description, model, role, position?}` |
| PUT      | `/api/agents/<id>`      | same fields except `id`                                            |
| DELETE   | `/api/agents/<id>`      | at least one agent must remain                                     |
| POST     | `/api/agents/order`     | `{ids: [...]}`, which must list every agent exactly once           |
| GET, PUT | `/api/agents/common`    | `{text}`: the shared rules                                         |
| GET      | `/api/agents/<id>`      | one agent + `position`, `prev`/`next`, `teams` using it, run `stats`, `model_ok` |
| GET      | `/api/presets`          | teams: built-in + saved (`{id, name, description, agents, builtin}`) |
| GET      | `/api/presets/<id>`     | one team                                                           |
| POST     | `/api/presets`          | `{name, description?, agents}`; the id comes from the name and is made unique |
| PUT      | `/api/presets/<id>`     | edit a saved team (`400` for built-in teams)                       |
| DELETE   | `/api/presets/<id>`     | saved teams only                                                   |
| GET      | `/api/agents/<id>/export` | the agent's `.md` file (download)                              |
| GET      | `/api/presets/<id>/export` | the team as a bundle `{format: "agents-atelier-team", version: 1, team, agents}` |
| GET      | `/api/export`           | `.zip`: `agents/*.md`, `agents/_common.md`, `agents/_presets.json`, `manifest.json` |
| POST     | `/api/import`           | `{name, data: base64, on_conflict: rename·replace·skip, apply}`; without `apply` it returns the plan only. Accepts `.md`, team `.json`, `.zip` (≤ 300 files, 5 MB unpacked) |
| GET      | `/api/models`           | the model list: `{default, options: [{id, label, kind: alias·full·custom, source, note}], alias_source}` |
| POST     | `/api/models/check`     | `{model}`: asks the Claude CLI to answer with that model; on success it's added to `agents/_models.json` |
| GET      | `/api/stats?project=<id>` | `{totals, agents: [per agent], recent: [last 30 runs]}`; omit `project` for all projects |

---

## 6. WebSocket — `/ws/<uuid>`

One socket per open studio tab. Every connected tab of a project receives every event.

### Client → server

| Message                                                      | Effect                                                      |
|--------------------------------------------------------------|-------------------------------------------------------------|
| `{"type":"prompt","text":"…","agents":[ids],"images":["uploads/…"]}` | start a run (refused if busy or if the name or description is missing) |
| `{"type":"stop"}`                                            | cancel the running task                                     |
| `{"type":"continue"}`                                        | resume the saved run                                        |
| `{"type":"restart"}`                                         | run the saved request again from the first agent            |
| `{"type":"retry","agent":"html","note":"…"}`                 | run one agent again                                         |

### Server → client

| Event            | Fields                                                    |
|------------------|-----------------------------------------------------------|
| `hello`          | `busy` (sent on connect)                                  |
| `user`           | `text`, `images`                                          |
| `run_start`      | `request`, `name`, `agents`, `done`, `resumed`, `retry?`, `note?` |
| `agent_start`    | `agent`                                                   |
| `tool`           | `agent`, `tool`, `target`                                 |
| `agent_text`     | `agent`, `text`                                           |
| `file_changed`   | `agent`, `file`                                           |
| `agent_done`     | `agent`, `text`, `stat`, `version`                        |
| `run_done`       | `retry?`                                                  |
| `run_stopped`    | `run` (summary), `retry?`                                 |
| `error`          | `text`, `run?` (summary, when the run can be resumed)     |
| `resume`         | `mode` (continue/restart), `from`, `request`              |
| `versions`       | a version was saved                                       |
| `restored`       | `version`, `backup`                                       |
| `needs_details`  | `text`: the project needs a name and description first    |

Every broadcast event carries `project`, `seq` and `ts`. The studio ignores events with `seq ≤ lastSeq`. After a
reconnect, it fetches `/api/projects/<id>` and replays any events it missed, which can't create duplicates.

The **run summary** is `{status, request, agents, done, current, next, error, resumable}`.

---

## 7. Versions and stats

### Versions (`server/versions.py`)

- A snapshot copies every file of `work/<uuid>/` except `uploads/` into `work/.versions/<uuid>/<NNNN>/`.
- A SHA-1 of all paths and contents is stored with each version. If it matches the previous version, no new version
  is created, so runs that don't change anything don't fill the list.
- At most **150** versions are kept; the oldest are deleted first.
- **Restore** takes a `backup` snapshot first, empties the site folder (keeping `uploads/`), then copies the version back.
- **Diff** uses `difflib.unified_diff` per file and reports `added`, `removed` or `changed` with line counts.

### Stats

Each agent run, whether it finished, was stopped or failed, appends an entry to the project's `stats`:

```json
{ "agent": "html", "status": "done", "ts": 1793…, "request": "…", "seconds": 18.1,
  "model": "claude-sonnet-5-5", "cost": 0.14422, "turns": 6, "api_seconds": 15.2,
  "tokens_in": 120345, "tokens_out": 1799 }
```

`cost` is Claude Code's `total_cost_usd` (an API-equivalent estimate). `tokens_in` includes cache reads and writes.
`/api/stats` adds these up per agent (runs, finished, time, cost, tokens, models) and overall.

---

## 8. Error handling

### Server

| Situation                        | Response                                                                 |
|----------------------------------|--------------------------------------------------------------------------|
| `HTTPException` (404, 409, …)    | JSON `{"detail"}` for `/api/*`; a styled HTML error page for browser pages |
| Invalid JSON or wrong field types | `422` with one readable sentence                                        |
| Request body over 8 MB           | `413`, checked from `Content-Length` before reading                      |
| Any other exception              | `500` with `{"detail", "ref"}`; the traceback is logged under that ref. A full disk gives a specific message |
| Damaged project file             | skipped in lists; opening it gives a `500` naming the file               |
| Damaged chat line                | skipped                                                                  |
| Versioning or stats failure      | logged, never stops a run (`safely()`)                                   |
| Bad WebSocket message            | an `error` event; the connection stays open                              |

Agent failures are turned into actionable messages by `explain_failure()`:
- Claude Code not found or not executable (`CLAUDE_BIN`)
- not logged in
- rate-limited or overloaded
- unknown model
- timed out

A failed run is saved as `error`, so it can be continued.

### Limits

| Constant         | Value     | Applies to                                     |
|------------------|-----------|------------------------------------------------|
| `MAX_BODY`       | 8 MB      | any request body                               |
| `MAX_IMAGE`      | 5 MB      | each reference image (PNG, JPG, WebP, GIF)     |
| `MAX_EDIT`       | 500 KB    | files loaded or saved by the Code tab          |
| `AGENT_TIMEOUT`  | 900 s     | one agent process                              |
| `MAX_VERSIONS`   | 150       | versions kept per project                      |
| stats            | 500       | stats entries kept per project                 |

### Front end

- **`api()` in `ui.js`:**
  - adds a 30-second timeout
  - turns a network failure into "Can't reach the server…"
  - reads JSON or HTML error bodies and always throws an `ApiError` with a readable `message` and `status`
- **`showError(el, err, retry)`** renders a "Couldn't load — Try again" block.
- A global `unhandledrejection` and `error` handler shows any unexpected error as a toast, at most one every 4 seconds.
- **The studio:**
  - shows a full-screen error if a project can't be opened, and redirects on 404
  - shows a banner when the WebSocket drops, then reconnects with backoff (1.5 s, 3 s, … up to 10 s) and catches up on missed events
  - saves code edits before starting a run, continue, retry or restore

---

## 9. Security model and prompt injection

Agents Atelier is a **single-user, local tool**. The main risk isn't the user. It's **prompt injection**: text the agents
read but the user didn't write (another agent's note, `plan.md`, page content, text inside an uploaded image) trying
to make an agent do something else. The defences are layered.

### 1. Tool isolation (enforced by the Claude CLI)

```
claude -p … --restricted --strict-mcp-config --disable-slash-commands
       --tools Read,Write,Edit,Glob,Grep --allowedTools Read Write Edit Glob Grep
       --disallowedTools Bash WebFetch WebSearch Task --permission-mode acceptEdits
```

| Flag                         | Effect                                                                                  |
|------------------------------|-----------------------------------------------------------------------------------------|
| `--restricted`               | no tools that run code, no WebFetch; ignores user/project/local settings; **file tools confined to the working directory** (the project folder) |
| `--tools …`                  | only these five built-in tools exist in the session                                    |
| `--strict-mcp-config`        | no MCP servers                                                                         |
| `--disable-slash-commands`   | no skills or slash commands                                                            |

This was checked against the real CLI: a `Read` of `/etc/hostname` is denied, while files in the folder can be read.
The model check (`POST /api/models/check`) runs with `--restricted --tools ""` (no tools at all).

### 2. Prompt structure (`build_prompt`, `SECURITY_RULES`)

- The prompt puts the project name and description, earlier requests, the request, team notes and image paths in
  tagged blocks: `<project>`, `<earlier_requests>`, `<user_request>`, `<team_notes>`, `<reference_images>`.
- `clean_text()` removes control characters, defuses any of those tags found inside the text (`</team_notes>` →
  `‹/team_notes›`), and caps lengths (notes 600 characters, request 4000).
- `SECURITY_RULES` is appended **last** to the system prompt, after `_common.md` and the agent's role. So it takes
  precedence and can't be removed by editing agent files. It says that blocks and file contents are data, not
  instructions; to stay in the folder; not to add external scripts, iframes, forms or tracking; and to report
  suspicious instructions.

### 3. Detection (`security` events)

- **Blocked actions:** Claude Code's `permission_denials` in the result (for example a read outside the folder) are
  sent as a `security` event and stored in the agent's stats (`denied`).
- **Page scan:** `scan_site()` runs before a run and after every agent, and reports **new** findings:
  - external `<script src>`, `<iframe>`, `<form action>`, `<link href>`, media `src`, CSS `url()` / `@import`
  - `fetch` / `sendBeacon` / `XMLHttpRequest` / `WebSocket` to another site
  - Google Fonts is allowed
- The studio shows each finding as a **Security check** card.

### 4. Containment of generated pages

Every `/work/…` response carries
`Content-Security-Policy: sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads`, without
`allow-same-origin`, plus `X-Content-Type-Options: nosniff`. A generated page therefore runs in an opaque origin,
whether it's in the preview iframe, a thumbnail (which also has `sandbox=""`, so no scripts) or its own tab. Its
JavaScript can't read or change the app through the API.

### 5. Request guards

- **Host check:** requests whose `Host` isn't `localhost`, `127.0.0.1` or `[::1]` get `421`, which blocks DNS
  rebinding. Add names with `ATELIER_HOSTS=a,b`.
- **Origin check:** state-changing requests (`POST` / `PUT` / `PATCH` / `DELETE`) and WebSockets with an `Origin` from
  another site get `403` (or WebSocket close `4403`).
- **Size limits:** request bodies are limited (8 MB).
- **Path checks:** file paths from the browser are resolved and must stay inside the project; uploads are checked
  against an allowed image type list and size.

The server binds to `127.0.0.1` and has no authentication: don't expose it to a network.

---

## 10. Extending

| You want to…                  | Do this                                                                                     |
|-------------------------------|---------------------------------------------------------------------------------------------|
| Add an agent                  | **New agent** on `/agents`, or add `agents/NN-name.md`                                      |
| Change what every agent knows | Edit **Shared rules** on `/agents` (`agents/_common.md`)                                    |
| Use another model for an agent | Set `model:` on that agent; use `AGENT_MODEL` for the rest                                 |
| Add an icon                   | Add a path to `ICONS` in `server/static/icons.js` (24×24 grid, stroke-based)                |
| Add an API endpoint           | Add a route in `server/main.py`, **before** the `app.mount("/static"…)` / `app.mount("/work"…)` lines |
| Add a WebSocket command       | Handle a new `type` in `handle_ws_message()` and a matching event in the studio's `handle()` |
| Change a limit                | Edit the constants at the top of `server/main.py` (`MAX_BODY`, `MAX_IMAGE`, `MAX_EDIT`, …) |

### Development notes

- **Tests:** `.venv/bin/python -m unittest discover -s tests -v`.
  - `tests/test_server.py` starts a server with `ATELIER_WORK` / `ATELIER_AGENTS` pointing to a temporary copy and
    `CLAUDE_BIN=tests/fake-claude`.
  - The fake CLI behaves like the real one. Markers in the prompt pick the behaviour: `[[fail]]`, `[[fail-once]]`,
    `[[crash]]`, `[[hang]]`, `[[slow]]`, `[[escape]]`, `[[inject-script]]`.
  - It logs every call to `$FAKE_CLAUDE_LOG`, so tests can check the exact flags and prompt.
- `ATELIER_WORK` and `ATELIER_AGENTS` move the data folders, for example for tests or a second instance.
- Run the server without `run.sh`: `cd server && ../.venv/bin/uvicorn main:app --reload --port 8001`.
- Logs go to the terminal (logger `agents-atelier`).
