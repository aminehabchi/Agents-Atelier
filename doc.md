# Agents Atelier — technical documentation

This document explains how Agents Atelier works inside: its architecture, the agent run pipeline, what is stored in
Postgres and on disk, the HTTP and WebSocket APIs, the agent file format, members' Claude keys and plans, versioning,
stats, error handling and security.
For what the app does and how to run it, see [README.md](README.md).

---

## 1. Architecture

```
 Browser (vanilla HTML/CSS/JS, no build step)
   ├─ HTTP  /api/…        JSON API: accounts, projects, files, versions, stats, agents, teams, marketplace, plans, admin
   ├─ WS    /ws/<id>      live events for one project; prompts, answers and run commands go up the same socket
   ├─ HTTP  /work/<id>/…  the generated site, served sandboxed (live preview iframe)
   └─ HTTP  /media/<id>/… reference images and browser-check screenshots (owner and admins only)
        │
 FastAPI server (server/main.py, one process, asyncio)
   ├─ per project:  at most one running task (a run or a retry); live state in memory
   ├─ per agent:    one `claude -p` subprocess, its JSON stream parsed line by line
   ├─ background:   the hourly purge of accounts whose 30 days are over
   ├─ Postgres:     accounts, projects, chat, versions, stats, agents, teams, marketplace, plans (server/db.py)
   └─ storage/:     files only: agents' instructions and each project's site, versions, uploads, checks
        │
 Claude Code CLI (`claude`), run with the project owner's own Claude API key (CLAUDE_KEYS=user)
 or the server's login (CLAUDE_KEYS=server); headless Chrome for the browser checks
```

| Part              | Technology                                                        |
|-------------------|-------------------------------------------------------------------|
| Server            | Python 3.10+, FastAPI, Uvicorn (`uvicorn[standard]` for WebSockets), asyncpg |
| Agents            | Claude Code CLI in print mode (`claude -p … --output-format stream-json`) |
| Database          | Postgres 14+ (`database/docker-compose.yaml`), schema and shared agents by numbered migrations |
| Files             | `STORAGE_DIR` (default `storage/`), each file listed with its SHA-256 in the `files` table |
| Front end         | Static HTML pages with inline CSS and JS, plus shared `base.css`, `site.css`, `theme.css`, `ui.js`, `icons.js`, `market.js`, `i18n*.js` |
| Other libraries   | Pillow (WebP copies), segno (QR codes), cryptography (members' keys), axe-core 4.10.2 (vendored, accessibility audit) |
| Generated sites   | Plain HTML, CSS and JS, which the agents' shared rules enforce    |

### Source files

```
server/
├── config.py         settings: reads .env (shell variables win), parses and checks every value
├── serve.py          starts uvicorn with HOST/PORT/LOG_LEVEL from config (used by run.sh)
├── main.py           FastAPI app: pages, JSON API, WebSocket, run pipeline, stats, analytics, error handling
├── db.py · migrate.py · manage.py   the connection pool, the migrations runner, the admin commands
├── storage.py        Postgres-backed project store (cached in memory) and the storage/ folder layout
├── legacy.py         imports projects from an earlier version's work/ folder, once
├── auth.py           accounts, sessions, email codes, profile, plans and plan requests, keys, deactivation
├── keys.py           members' Claude API keys: check, encrypt (Fernet), clean environment for each run
├── plans.py          the three plans and their limits
├── collab.py         people on a project: owner, editors, viewers, invitations (in memory, Postgres-backed)
├── share.py          share links (read-only site for people without an account) and pinned comments
├── billing.py        PayPal subscriptions: checkout, return, webhooks, cancellation, payments received
├── oauth.py          log in or sign up with Google or GitHub (authorization code flow, verified emails only)
├── limits.py         per-IP limits and throwaway email domains
├── agents.py         agents and teams: parser, validation, ordering, export and import
├── orchestrator.py   the Orchestrator's prompt, its decision rules, forms, confirmations and template blanks
├── market.py         the marketplace: catalogue, profiles, publishing and review, hires, ratings, estimates
├── models.py         the model list and model checks
├── versions.py       code snapshots: create, list, diff, restore, prune
├── browser_check.py  headless Chrome over CDP: errors, layout measurements, axe-core, screenshots
├── contact.py        the contact details the contact agents need, and the form that asks for them
├── images.py         WebP copies of the user's photos
├── qr.py             QR codes drawn from the QR agent's qr.json
├── vendor/axe.min.js axe-core (MPL-2.0)
└── static/
    ├── landing.html · dashboard.html   /              the landing page, or the dashboard once logged in
    ├── projects.html · new-project.html /projects…   projects list and the new-project form
    ├── studio.html   /p/<uuid>    the studio: chat, preview, code, project, research, versions, stats, share, team
    ├── share.html · share-pin.js   /s/<token>   a share link's page; the script that places pins inside the shared site
    ├── hire.html · hire-profile.html   /hire…       the marketplace and its profiles
    ├── agents.html · teams.html        /agents… /teams…   agent and team pages
    ├── profile.html  /profile     account, plan, Claude key and spending, security, data and deletion
    ├── users.html · analytics.html     /users /analytics   admins: accounts, plans, revenue and monitoring
    ├── auth.html · legal.html · guide.html   login, sign-up, reset; terms and privacy; the user guide
    ├── base.css · site.css · theme.css design tokens, shared components, page layout
    ├── ui.js         api(), toast(), dialogs, planPrompt(), showError(), theme, command palette, pagination, lazy loading
    ├── market.js     marketplace helpers: Start a job, hiring, "choosing for a project"
    ├── i18n.js · i18n-fr.js       translation of the pages (French)
    └── icons.js · logo.svg   hand-drawn SVG line icons, logo and favicon
```

**Pagination and lazy loading.** Every list uses the same pager (`pagerHTML` in `ui.js`, styles in `base.css`).
Projects, versions and "recent agent runs" are paged **on the server** (`page`, `per_page`; the answer carries
`page, pages, per_page, total`). Agents, teams and the per-agent stats table are paged in the browser, since they
are small and already loaded. Each list remembers its "per page" choice in `localStorage` (`perPage:<list>`).
Lazy: project thumbnails (iframes) load when they near the screen; images use `loading="lazy"`; the landing page
asks for its 6 recent projects only when that section scrolls into view; the studio replays the whole chat once
(to rebuild agent states) but keeps only the last 40 messages in the page and puts earlier ones back 30 at a
time when you scroll up (`foldHistory` / `showEarlier`).

---

## 2. Data

Postgres (`server/db.py`, `database/docker-compose.yaml`) holds everything except the files. The schema and the shared
agents and teams are built by the migrations in `server/migrations/` (`server/migrate.py`), applied on startup, once
each, in order, and recorded in `schema_migrations`. `manage.py make-agents-migration` turns edits to `agents/*.md` and
`agents/_presets.json` into a new migration; an agent or team an admin edited (`origin = 'app'`) or deleted
(`shared_removed`) is left alone.

| Table | What |
|---|---|
| `users` | email, scrypt password hash, role, profile answers, language, theme; the **encrypted Claude key** (`claude_key`, `claude_key_hint`, `claude_key_at`), `plan`, `plan_request`, `run_limit`, and `deactivated_at` / `delete_after` |
| `sessions`, `email_codes`, `reset_codes` | logins (token hashes) and the 6-digit codes |
| `plan_events` | every plan change: user, from, to, by an admin or the member, time (revenue analytics) |
| `projects` | name, description, owner, pages, brief, brand kit, assets, the project's team (`agents`), showcase, `run`, `extra` (contact details, pending questions, the latest check…) |
| `project_requests` · `project_events` | every request, and the chat events replayed by the studio |
| `agent_runs` | one row per agent run: status, time, cost, tokens, model, Critic score |
| `versions` · `files` | saved versions, and every file in `storage/` with its own uuid, size and SHA-256 |
| `agents` · `teams` | shared (`owner_id` NULL) and members' agents and teams; instructions live in `storage/agents/` |
| `listings`, `hires`, `agent_ratings` … | the marketplace |

`storage.Store` loads every project into memory at startup and writes changes through to Postgres, so reads are fast and
a crash never leaves a half-written project. Files:

```
storage/
├── .secrets/key_secret              the Fernet key for members' API keys (when KEY_SECRET is empty), mode 600
├── claude-home/<user-uuid>/         each member's own Claude Code folder (CLAUDE_CONFIG_DIR and HOME of their runs)
├── agents/<agent-uuid>/instructions.md
└── works/<project-uuid>/
    ├── site/                        the website the agents edit (served at /work/<project-uuid>/)
    │   └── assets/                  the user's images, their WebP copies (-480/-960/-1600.webp), QR codes (qr-*.svg)
    ├── versions/<version-uuid>/     a full copy of site/ at one moment
    ├── uploads/<file-uuid>.png      reference images attached to prompts
    └── checks/<check-uuid>/         the latest browser-check screenshots
agents/                              the source of the shared agents (read by migrations), _common.md, _orchestrator.md
```

### A project, as the server sees it

```jsonc
{
  "id": "7d49ead2-…", "owner": "<user uuid>",
  "name": "Bean There coffee",      // required, ≤ 80 chars
  "description": "A cozy café…",    // required, ≤ 300 chars; sent to every agent
  "pages": [{"title": "Home", "file": "index.html"}, {"title": "About", "file": "about.html"}],
  "brief": {"audience": "…", "goal": "…"}, "brand": {"colors": ["#…"], "heading_font": "…"},
  "assets": [{"path": "assets/hero.jpg", "kind": "photo", "variants": [{"path": "assets/hero-480.webp", "width": 480}]}],
  "contact": {"whatsapp": "+212…", "email": "…"}, "contact_skipped": ["telegram"],
  "agents": ["brief", "ux", "design", …],   // the project's team
  "history": ["a landing page…"],           // finished requests (the last 6 go into the prompt)
  "run": {                                  // the latest run, kept for Continue / Start again
    "request": "…", "images": ["uploads/x.png"], "agents": ["ux", "html"], "done": ["ux"], "notes": ["- UX agent: …"],
    "plan": {"agents": […], "notes": {…}, "reason": "…"}, "review_round": 0, "send_back": {},
    "current": "html", "status": "running | stopped | error | interrupted | done", "error": "", "started": 1793…
  },
  "pending": null, "last_exchange": null    // an Orchestrator form or confirmation waiting for an answer
}
```

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
- Agents live in Postgres (`agents` table, instructions in `storage/agents/<uuid>/instructions.md`). `load_agents(user)`
  returns the shared agents plus the member's own, in order, so edits apply to the next run without a restart. A run
  takes a snapshot of the team when it starts.
- The files in `agents/` (`NN-name.md`, `_presets.json`) are the source of the shared agents and teams: migrations
  copy them into the database. Files whose names start with `_` or `.` aren't agents.
- An agent can set `tools: web` (Research): it then gets WebSearch and WebFetch, may only write `research.md` and
  `plan.md`, and the server undoes any other change it makes.

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
and Full. The 25 shared use-case teams (with a starting prompt and a category) come from `agents/_presets.json` through
the migrations; members' teams are rows in the `teams` table. Unknown agent ids are dropped when teams are loaded.

---

## 4. The run pipeline

### One agent = one Claude Code process

`run_agent()` starts:

```
claude -p "<prompt>"
  --output-format stream-json --verbose
  --restricted --strict-mcp-config --disable-slash-commands
  --tools Read,Write,Edit,Glob,Grep[,WebSearch,WebFetch]
  --allowedTools Read Write Edit Glob Grep
  --disallowedTools Bash Task [WebFetch WebSearch] Read(//proc/**) Read(//sys/**) Read(//etc/**) …   (DENY_READS)
  --permission-mode acceptEdits
  [--add-dir uploads/ --add-dir checks/]
  --exclude-dynamic-system-prompt-sections
  --append-system-prompt "<language rule><_common.md>\n\n<agent role>\n\n<SECURITY_RULES>"
  [--model <agent.model or AGENT_MODEL>]
```

The working directory is `storage/works/<uuid>/site/`, so file tools are confined to the project folder.

**Whose Claude account.** With `CLAUDE_KEYS=user`, `claude_env(meta)` builds the process's whole environment from
scratch (`keys.env_for`): a few safe variables (`PATH`, locale, proxies), the project owner's decrypted key as
`ANTHROPIC_API_KEY`, and `CLAUDE_CONFIG_DIR` / `HOME` set to `storage/claude-home/<owner>/`. Nothing else from the
server's environment (database URL, SMTP password, `KEY_SECRET`) reaches an agent. No key → `NoClaudeKey`: the
WebSocket answers `needs_key` before anything runs. The Orchestrator and `POST /api/models/check` use the same
environment. With `CLAUDE_KEYS=server`, the process inherits the server's environment and login.

The **prompt** is built from (each in its own tagged block, see §9):
- the site's pages (`<site_pages>`: one page, or the list of files with the shared header/nav/footer rule)
- the project name and description
- the last 6 finished requests
- the current request
- the notes left by earlier agents in this run ("- CSS agent: …")
- the reference images, which the agent is told to open with `Read`
- `<project_brief>`, `<brand_kit>`, the contact details and `<project_assets>` (with the WebP copies)
- `<run_plan>` (the Orchestrator's reason and a note for this agent) or `<review_feedback>` / `<send_back>` for the Critic's rounds
- `<browser_check>` for Responsive, Accessibility, QA and the Critic (see below)

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

1. Snapshot the selected agents, in file order. Read the owner's plan (`plans.of`): agents with web tools are dropped
   on Starter (a `notice` says so), browser checks run only before the Critic on Starter, the plan sets how many
   versions are kept, and the owner's spending limit is read. Save `run` with `status: running`, broadcast `run_start`.
2. Save a version labelled **"Before: <request>"**. It's skipped if the code hasn't changed since the last version.
3. Step by step until every agent is in `done`. A step is the next agent in team order, plus the agents right after it
   that the Orchestrator grouped with it in `plan.parallel` (`orchestrator.next_step`):
   1. set `run.current` and `run.running`; if an agent of the step gets a browser check, run it once and give it to
      each of them (`browser_check`); broadcast `parallel` for a group, then `agent_start` for each agent (`with`: the
      others); run them at the same time (`asyncio.gather`); then draw any QR codes they asked for (`qr.json`)
   2. append each finished agent's note, add it to `done`, save the run state. Every agent of a step reads the notes left
      before the step. If one failed, the others' work is kept and the run stops with `error` (Continue runs the failed one)
   3. save one version **"After <Agent> + <Agent>"**, then broadcast `agent_done` for each, with that version's number and
      the agent's own stats (`last_stat(pid, agent)`)
   4. for the Critic: parse its `REDO:` lines and send that work back (up to `REVIEW_ROUNDS`)
   5. add the agent's cost to the run's total; at the owner's limit, with agents left, stop as `stopped` and broadcast
      `limit` then `run_stopped` (Continue goes on)
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

**Before a run** (`run_prompt`): template `[blanks]` become a form; on a follow-up the Orchestrator
(`run_orchestrator`, read-only tools) decides `intent` and agents, and may ask a form (`questions`) or wait for a yes
(`confirm`); `orchestrator.decide()` adds the server's rules (Brief first, dependents, QA and the Critic after code
changes). If an agent in the plan needs a contact detail that's missing (`contact.missing`), a form asks for it first.
The pending question is kept in `pending` / `last_exchange` until the `answer` message arrives.

**Parallel steps** (`orchestrator.py`, "Parallel steps"): the Orchestrator may answer `parallel: [[ids], …]`.
`clean_parallel()` keeps a group only if its agents are in the run, none is in `SOLO` (Brief, Research, Auditor, Tidy,
Translator, UX, HTML, QA, Critic), none adapts to another (`DEPENDS`, followed through), and it has 2 to
`PARALLEL_AGENTS` agents (default 3; 1 turns this off). `next_step()` only joins agents that are next to each other in
run order. A **first build** doesn't ask the Orchestrator what to run, so `schedule_first_build()` asks it only for the
groups (`<first_build>` in the prompt) while Brief works, and only when two neighbours could pair (`could_pair`); the
groups apply from the next step (event `schedule`). Each project keeps a set of `claude` processes (`runs[pid]["procs"]`);
Stop kills them all.

**Browser check** (`browser_check.check_site`): every page at 1280px and 390px in headless Chrome over CDP, collecting
console errors, failed files, broken images, dead links and anchors, sideways overflow with the element too wide, a
layout probe (overlaps, header wrapping, blank bands, narrow sections, a lonely last card) and axe-core results on
desktop. `prompt_block()` turns it into data for the agent; `summary()` into the chat card.

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
| GET    | `/`           | the landing page, or `dashboard.html` for a confirmed account       |
| GET    | `/projects/new` | the form that creates a project (`new-project.html`)              |
| GET    | `/profile`    | your details, password, other devices, delete account (`profile.html`) |
| GET    | `/forgot`     | reset a forgotten password with an emailed code (`auth.html`)       |
| GET    | `/guide`      | guide (needs a confirmed account)                                   |
| GET    | `/studio`     | redirect to `/projects` (the studio is only opened from a project)  |
| GET    | `/hire`, `/hire/agents/<n>`, `/hire/teams/<n>` | the marketplace and profiles            |
| GET    | `/users`, `/analytics` | admins only                                                  |
| GET    | `/login`, `/register`, `/confirm`, `/terms`, `/privacy` | account and legal pages             |
| GET    | `/work/<uuid>/…` | the generated site (no-store caching, sandbox CSP), also `/work/.versions/<uuid>/<NNNN>/…` |
| GET    | `/media/<uuid>/<area>/…` | uploads and check screenshots, to who can open the project       |

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
| GET    | `/api/projects/<id>/download`          | —                                    | `.zip` of the site, `409` while busy; on Starter each page gets a "Made with Agents Atelier" line |
| POST   | `/api/projects/import`                 | `{name, description, files…}`        | a project from an existing site           |
| POST   | `/api/projects/<id>/assets`            | `{name, data, kind}`                 | logo or photo; photos get WebP copies     |
| PUT    | `/api/projects/<id>/contact`           | `{whatsapp?, phone?, email?, telegram?}` | validated; an empty value = "not given" |
| GET/PUT | `/api/projects/<id>/brand` (+ `/logo`, `/copy`) | the brand kit               |                                           |
| GET/PUT | `/api/projects/<id>/ratings`          | thumbs up/down and a comment per agent | owner only                             |

`POST /api/projects` and `/import` answer `403 {"detail", "upgrade": "pro"}` past the plan's project limit.

Each project in a list has `id, name, description, created, updated, busy, paused, prompts, agents, files, size, has_preview`.

A file's `kind` is `text`, `image`, `binary` or `other`. `content` is only present for text files of 500 KB or less;
images get a `url` instead.

### Versions

| Method | Path                                             | Notes                                                    |
|--------|--------------------------------------------------|----------------------------------------------------------|
| GET    | `/api/projects/<id>/versions?page=&per_page=`    | newest first: `{n, label, kind, agent, request, ts, files, size, hash}`; with `page`, one page: `{items, page, pages, per_page, total}` (per_page ≤ 100, page clamped) |
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
| GET      | `/api/market…`          | the catalogue, profiles, `hire`, `publish` (Agency, `{kind, id, category, price}`: 0 or $1–$100), `withdraw`, `pending` and `review` (admins). Items carry `price` and `bought`; hiring a paid one not bought yet answers `402` |
| POST     | `/api/market/{agents,teams}/<pk>/buy` | one-time PayPal order (`accounts/sales.py`): `{approve}` (PayPal's page) or `{owned: true}`. PayPal returns the buyer to `/market/paypal/return?token=<order>`, which captures it and copies the agent (locked) or team |
| GET      | `/api/earnings?days=`   | the Earnings dashboard: `totals` (sales, gross, earned 70%, platform 30%, refunds, balance, paid out), `items` per agent and team, `series` per day, `recent` sales, `payouts`, `purchases` |
| PUT, POST | `/api/earnings/payout-email`, `/api/earnings/payout` | `{email}`; ask for the whole balance (from $10, one request at a time) |
| GET, POST | `/api/admin/payouts`, `/api/admin/payouts/<id>` | admins: the payout queue; `{decision: paid·rejected, reference, note}` (paid needs the PayPal transaction id) |
| GET      | `/api/stats?project=<id>&page=&per_page=` | `{totals, agents: [per agent], recent: [last 30 runs]}`; omit `project` for all projects. With `page`, `recent` is one page of **all** runs (newest first) and `recent_pager` = `{page, pages, per_page, total}` |

Creating or editing agents and teams (`POST/PUT /api/agents`, `/api/presets`, `/api/import`) needs the Pro plan, and
publishing the Agency plan: otherwise `403 {"detail", "upgrade"}`, which `ui.js` turns into an "Available on Pro" window.

### Accounts, keys and plans

| Method | Path | Notes |
|--------|------|-------|
| POST | `/api/auth/register`, `/login`, `/confirm`, `/resend`, `/forgot`, `/reset`, `/logout` | sign-up, login, codes. A login during the 30 days of a deactivated account cancels its deletion and answers `user.reactivated: true` |
| GET/PATCH | `/api/auth/me`, PATCH `/api/auth/preferences` | the account (`plan`, `claude_key: {set, hint, checked}`, `run_limit`, `plan_request`, `delete_after`…); details; language and theme |
| POST | `/api/auth/password`; GET/DELETE `/api/auth/sessions` | change password; other devices |
| PUT/DELETE | `/api/auth/claude-key` | `{key}`: checked with one tiny call (`keys.check`), stored encrypted; `400` with a safe message otherwise |
| PUT | `/api/auth/run-limit` | `{limit}`: $0.50 up to the plan's maximum (fixed on Starter) |
| POST | `/api/auth/plan` · DELETE `/api/auth/plan-request` | `{plan}`: lower applies at once, higher becomes a request (admins are emailed); cancel the request. With PayPal on, a paid plan answers `400` (use the PayPal route) and Starter cancels the subscription first |
| GET | `/api/plans` | `{plans, keys, payment: "paypal"\|"request"}`, plus `usage.projects` and `subscription` for a logged-in member |
| POST | `/api/billing/paypal/subscribe` | `{plan}` (pro, agency): creates a PayPal subscription (`custom_id` = the member) → `{approve, id}`; the page sends the member to `approve` |
| GET | `/billing/paypal/return?subscription_id=` | PayPal sends the member back here: the server reads the subscription, checks it's theirs and active, switches the plan, redirects to `/profile?paid=active\|pending\|cancelled\|error#plan` |
| POST | `/api/billing/paypal/webhook` | public; the signature is checked with PayPal (`PAYPAL_WEBHOOK_ID`), each event handled once (`billing_events`). `BILLING.SUBSCRIPTION.*` keeps the plan in step (active → the plan, cancelled/suspended/expired → Starter, the old subscription is cancelled after a switch); `PAYMENT.SALE.COMPLETED/REFUNDED/REVERSED` go to `billing_payments` |
| GET | `/api/auth/export` | a .zip of everything about you |
| GET | `/api/auth/oauth/<google\|github>/start?next=` | redirect to the provider with a random `state` (base64 JSON in the `atelier_oauth` cookie, 10 minutes) |
| GET | `/api/auth/oauth/<provider>/callback` | checks the state, exchanges the code, reads the **verified** email: the linked account (`user_identities`), else the account with that email (linked and confirmed), else a new confirmed account with no password; starts a session and redirects to `next` (a path on this site only). Errors go back to `/login?oauth_error=` |
| GET | `/api/auth/identities` | the providers linked to your account |
| POST | `/api/auth/delete` | `{password}`: deactivates the account, ends every session; `{delete_after, days}` |

### Admin

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/users` | every account |
| PATCH | `/api/users/<id>` | `{role}`, `{plan}` (also clears a request, logs a `plan_events` row) or `{decline_request: true}` |
| GET | `/api/admin/analytics?days=` | sign-ups, activity, runs, cost per day, audience, top accounts |
| GET | `/api/admin/revenue?days=` | monthly revenue, run rate, paying members, revenue per member, by plan, upgrades/downgrades, waiting requests, revenue per day, latest changes, and `received` (money PayPal collected in the period) |

### Run estimate

| Method | Path | Notes |
|---|---|---|
| GET | `/api/projects/<id>/estimate?agents=a,b` | about what a run of these agents (default: the project's team; Brief always) costs on the owner's Claude account and how long it takes: per agent its last 5 runs on this project, else its runs everywhere (`market.agent_estimate`), else a default for its model; `{agents, cost, low, high, seconds, first_build, limit, over_limit, measured, count}`. The studio shows it next to the composer and on the Orchestrator's plan card (`static/studio-extras.js`, which also holds the first-run studio tour) |

### People, share links and comments

| Method | Path | Who | Notes |
|--------|------|-----|-------|
| GET | `/api/projects/<id>/people` | anyone on it | `{owner, members:[{id,email,name,role}], invites:[{email,role}], active, my_role, can_invite}` |
| POST | `/api/projects/<id>/people` | owner | `{email, role: editor\|viewer}`: an existing account joins at once, any other email waits in `project_invites` until it confirms an account. Needs Agency (`403` + `upgrade`), 20 people at most |
| PATCH / DELETE | `/api/projects/<id>/people/<user>` | owner | change the role / remove |
| DELETE | `/api/projects/<id>/membership` · `/api/projects/<id>/invites?email=` | the member · owner | leave the project · cancel an invitation |
| GET / POST | `/api/projects/<id>/share` | owner, editors | list links (with `views`, `comment_count`, `state`, `version_n`) · `{label, days: 1\|7\|30\|90\|0, comments, version}` creates one; `version` is `"live"` (default), `"now"` (saves the site as a version, refused with 409 while agents work) or a version number. A frozen link's version is never pruned while the link isn't revoked (`share.pinned`, `Versions.pinned`) |
| DELETE | `/api/projects/<id>/share/<link>` | owner, editors | turn a link off |
| GET | `/api/projects/<id>/comments` | anyone on it | every pinned comment |
| PATCH / DELETE | `/api/projects/<id>/comments/<comment>` | owner, editors | `{resolved}` · delete |
| GET | `/s/<token>`, `/s/<token>/info`, `/s/<token>/site/…` | anyone with the link | the share page, `{name, pages, comments, expires, built}`, the site (sandboxed like `/work/`, web pages get `share-pin.js`) |
| GET / POST | `/s/<token>/comments` | anyone with the link | the link's comments · `{text, author, page, selector, x, y, doc_x, doc_y, viewport, snippet}` (30 an hour per address) |
| GET | `/api/admin/monitoring?days=` | health (database, Claude Code, Chrome, disk, uptime, busy projects), runs by status, typical and 90th-percentile agent time, failures per day, agents with the most problems, latest errors, members with a key, accounts being deleted |

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
| `{"type":"answer","answers":{…}}` / `{"type":"answer","confirm":true}` / `{"type":"answer","cancel":true}` | answer the open form, or confirm or cancel a planned run |

A **viewer** may connect and watch, but `prompt`, `stop`, `continue`, `restart`, `retry` and `answer` answer
`{"type":"error","viewer":true}`.

Before `prompt`, `continue`, `restart`, `retry` or `answer` starts anything, the server checks there's a Claude key to
run with; if not it answers `needs_key` (`{text, mine}`) and nothing runs. An admin who runs an older project without
an owner becomes its owner.

### Server → client

| Event            | Fields                                                    |
|------------------|-----------------------------------------------------------|
| `hello`          | `busy` (sent on connect)                                  |
| `user`           | `text`, `images`                                          |
| `run_start`      | `request`, `name`, `agents`, `done`, `resumed`, `retry?`, `note?` |
| `agent_start`    | `agent`, `fix?`, `with?` (the agents working alongside it) |
| `parallel`       | `agents`: a step where these agents work at the same time |
| `schedule`       | `groups`: on a first build, the Orchestrator's groups arrived |
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
| `needs_key`      | `text`, `mine`: no Claude API key to run with              |
| `orchestrating`  | the Orchestrator is deciding                              |
| `plan`           | `intent`, `agents`, `notes`, `parallel`, `reason`, `reply`, `questions?`, `confirm?`, `needs?` |
| `answer`         | the user's answers to a form                              |
| `browser_check_start`, `browser_check` | `agent`, `text`, `problems`, `screenshots`   |
| `send_back`      | the Critic sent work back: `agent`, `round`, `rounds`, `items` |
| `pages`          | the list of pages changed                                 |
| `security`       | a blocked action or a page-scan finding                   |
| `notice`         | `text`, `upgrade?`: e.g. Research skipped on Starter      |
| `limit`          | `text`, `spent`, `limit`: the run reached the owner's spending limit |
| `comment`        | not saved in the chat: `comment` (new or resolved), `new?`, or `deleted` (an id) |
| `people`         | not saved in the chat: someone joined, left or changed role |

Every broadcast event carries `project`, `seq` and `ts`. The studio ignores events with `seq ≤ lastSeq`. After a
reconnect, it fetches `/api/projects/<id>` and replays any events it missed, which can't create duplicates.

The **run summary** is `{status, request, agents, done, current, running, next, error, resumable}`.

---

## 7. Versions and stats

### Versions (`server/versions.py`)

- A snapshot copies every file of `storage/works/<uuid>/site/` into `versions/<version-uuid>/`.
- A SHA-1 of all paths and contents is stored with each version. If it matches the previous version, no new version
  is created, so runs that don't change anything don't fill the list.
- At most `MAX_VERSIONS` (150) are kept, fewer on a plan that keeps fewer (Starter 10, Pro 100); the oldest are
  deleted first.
- **Restore** takes a `backup` snapshot first, empties the site folder (keeping `uploads/`), then copies the version back.
- **Diff** uses `difflib.unified_diff` per file and reports `added`, `removed` or `changed` with line counts.

### Stats

Each agent run, whether it finished, was stopped or failed, appends an entry to the project's `stats`:

```json
{ "agent": "html", "status": "done", "ts": 1793…, "request": "…", "seconds": 18.1,
  "model": "claude-sonnet-5-5", "cost": 0.14422, "turns": 6, "api_seconds": 15.2,
  "tokens_in": 120345, "tokens_out": 1799 }
```

`cost` is Claude Code's `total_cost_usd`: with `CLAUDE_KEYS=user` it's what the run cost on the owner's Claude account. `tokens_in` includes cache reads and writes.
`/api/stats` adds these up per agent (runs, finished, time, cost, tokens, models) and overall.

---

## 8. Error handling

### Server

Every error is JSON `{"detail": "<one readable sentence>"}` for `/api/*` (plus `upgrade` on plan errors, `ref` when
something was logged), and a styled HTML page for browser pages. No answer carries a traceback, SQL, a key or a server
path (`keys.scrub` removes API keys, `scrub_paths` the app, storage and home folders).

| Situation                        | Response                                                                 |
|----------------------------------|--------------------------------------------------------------------------|
| `HTTPException` (404, 409, …)    | its status and message                                                    |
| Unknown `/api/` address          | `404` "There's nothing at this address. Check the URL."                  |
| Wrong method                     | `405` "This address doesn't accept DELETE requests (it accepts GET, PATCH)." with an `Allow` header listing every route's methods |
| Body that isn't a JSON object, missing, or invalid JSON | `422`: "Send the details as a JSON object, like {"name": "…"}." · "This request needs a JSON body." · "The request body isn't valid JSON." |
| Wrong query or path types        | `422`: "“page” must be a whole number.", "“full” must be true or false.", "“x” is required." |
| Wrong types inside a body        | `400` with the field's own message (lists that aren't lists, numbers too large, `Infinity`…) |
| A malformed or unknown project id | `404` "This project doesn't exist (it may have been deleted)." — the same for another member's project |
| A marketplace id no database can hold | `404` "This isn't in the marketplace (it may have been withdrawn)." |
| A value Postgres refuses (NUL character, number out of range) | `400` "One of the values you sent can't be saved (it may contain invalid characters or be too large). Check it and try again." |
| Postgres down, restarting or too slow (asyncpg connection errors, refused connections, timeouts) | `503` "The database isn't reachable right now. Try again in a minute." with `Retry-After: 30`; logged as one line with a ref, no traceback |
| Busy project / size / rate        | `409` while agents work · `413` too large (`Content-Length` checked before reading) · `429` with `Retry-After` |
| Upstream failures                 | `502` (PayPal, SMTP, OAuth providers: every outbound call has a timeout of 20–30 s) |
| Full disk                         | `507` "The disk is full, so the server couldn't save. Free some space and try again." |
| Any other exception (a bug)       | `500` "Something went wrong on the server. Details are in the server log under ref xxxxxx."; the traceback is logged under that ref |
| Damaged .zip (import)             | `400` naming the file that couldn't be read (damaged or password-protected) |
| Damaged project file / chat line  | skipped                                                                  |
| Versioning or stats failure       | logged, never stops a run (`safely()`)                                   |

`explain_exception()` in `main.py` makes this choice; `db.unavailable()` and `db.bad_value()` tell an outage or a refused
value from a bug. The database errors are also registered by class, so uvicorn doesn't log a second traceback.

**Database.** Connecting gives up after 10 s and every query after 30 s (`db.init`), so nothing hangs on a dead
Postgres. The store's writer retries a change for about a minute while Postgres is unreachable, in order, before
logging it as lost. When it's back, requests work again with no restart.

**WebSocket.** A binary frame, a message over 256 KB, invalid JSON, a message that isn't an object, a missing or unknown
`type`, `agents`/`images` that aren't lists or an unknown agent to retry each get `{"type": "error", "text"}` and the
socket stays open. An unexpected failure answers "Something went wrong on the server (ref …)" (or the 503 text) and is
logged. The session and the project are checked on every message: logged out → close `4401`; project deleted or no
longer shared → `{"type": "error", "gone": true, "text": "This project doesn't exist (it may have been deleted)."}` then
close `4404`. A tab that doesn't take an event within 10 s is closed (`1013`), so it reconnects and catches up.

**Background work.** Every run task (`prompt`, plan, continue/restart, retry) runs inside `guarded_run()`: whatever
escapes is logged with the project and what was running, a run left `running` is saved as `error`, and the studio gets
an `error` event. `run_error_text()` shows our explained failures (`RuntimeError`) as they are and anything else as
"The run stopped because of a problem on the server (ref …). Press Continue to try again; …". Odd lines from the Claude
CLI are skipped instead of failing the run. `purge_loop` goes on after an account that can't be erased; a PayPal webhook
that fails is forgotten so PayPal's retry handles it; background emails log their failures; a Chrome that fails to
start is killed.

Agent failures are turned into actionable messages by `explain_failure()`:
- Claude Code not found or not executable (`CLAUDE_BIN`)
- a refused API key, no credit left, or a key without access (`keys.explain`); any `sk-ant-…` text and server paths are scrubbed first
- not logged in (`CLAUDE_KEYS=server`)
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

### Accounts and access

- **Sessions:** an HttpOnly, SameSite=Lax cookie with a random token; only its SHA-256 is stored. Set `COOKIE_SECURE=true`
  behind HTTPS. Passwords are hashed with scrypt.
- **Email codes:** 6 digits, stored hashed, valid `CODE_MINUTES`, 5 tries, one new code a minute. 10 failed logins lock an
  account for 15 minutes.
- **Password resets:** anyone can ask for one, so `/api/auth/forgot` answers the same whether or not the account exists and
  sends in the background. Beyond 5 tries a code, an account gets 5 codes and 15 wrong tries per 24 hours. A reset also confirms
  the address and ends every session; a change from `/profile` ends every session but the current one. Both email a notice.
- **Access checks:** one middleware (`_check_access` in `main.py`) guards every route: everything is private unless listed
  as public. Another user's project answers 404, as if it didn't exist. The WebSocket checks the session and the owner when
  it connects and again on every message.
- **`/work/<uuid>/` is reached by its unguessable project id, not by session.** Generated pages run in a sandbox with no
  origin, so browsers don't send the session cookie for their CSS, scripts and images. Anyone given such a link can see that
  site's files, including `plan.md`. `/work/.projects/` is never served.
- **Roles** (`collab.role_of`): owner, admin, editor, viewer. The same middleware refuses every change to a project
  (`POST`, `PUT`, `PATCH`, `DELETE` under `/api/projects/<id>`) by a viewer with `403`; inviting, removing people and
  deleting the project are the owner's (or an admin's). Memberships count only while the owner's plan includes `members`
  (Agency): after a move down, the people they invited get 404 until the owner is back on Agency.
- **Share links:** the token (32 random URL-safe characters) is the only secret; a link stops working when it expires or
  is turned off (cached 10 seconds). Its site is served sandboxed with `noindex` and `no-referrer`; files outside the site
  and browser-check screenshots are never served. Visitors get no cookie. Comments are plain text, length-limited, positions
  clamped, limited per address; they reach the agents only when someone on the team presses **Ask the team** and sends it.
- **Payments:** the server never sees card data. A subscription id from the return URL is only trusted after PayPal says
  it belongs to this member (`custom_id`) and is active; webhooks are trusted only after PayPal verifies their signature.
- **Runs** always use the project owner's agents and Claude key, even when an admin or an editor starts them.
- **Members' API keys** are encrypted with Fernet (`KEY_SECRET`, or a generated key in `storage/.secrets/`, mode 600),
  never returned to the browser (only `sk-ant-…` plus the last four characters), never logged, and scrubbed from every
  error message.
- **Rate limits** (`limits.py`): per address for sign-ups, logins (also Google/GitHub), resets and share-link comments; per
  member (`limits.key_for`, per address when logged out) for every `/api/` call (`API_REQUESTS_PER_MINUTE`, in the
  middleware; PayPal's webhook is exempt), runs started over the WebSocket (`RUNS_PER_HOUR`, an `error` event with
  `rate_limited`), invitations, share links and PayPal checkouts. Counters live in memory, per server process.
- **Accounts without a password** (made with Google or GitHub, `password_hash` empty, `has_password: false`): their
  password login always fails; changing the password or deleting the account doesn't ask for a current one.
- **Plans** are enforced on the server (`plans.need`, `check_project_limit`, the run pipeline); the pages only explain them.
- **Agents' instructions** (`plans` feature `instructions`, `sees_instructions` in `main.py`): on Starter, `/api/agents?full=1`,
  `/api/agents/<id>` and the marketplace profiles answer `role: ""` with `instructions_locked: true` (name, description,
  stats, projects and ratings stay); `/api/agents/common` and the three exports answer `403` with `upgrade: "pro"`.
- **Deleting an account** deactivates it and ends its sessions; `purge_loop()` deletes it with its projects, files,
  brand kit and Claude folder after `ACCOUNT_DELETE_DAYS`, unless a login cancels it first.

### Prompt injection

The main risk isn't the user. It's **prompt injection**: text the agents
read but the user didn't write (another agent's note, `plan.md`, page content, text inside an uploaded image) trying
to make an agent do something else. The defences are layered.

### 1. Tool isolation (enforced by the Claude CLI)

```
claude -p … --restricted --strict-mcp-config --disable-slash-commands
       --tools Read,Write,Edit,Glob,Grep --allowedTools Read Write Edit Glob Grep
       --disallowedTools Bash WebFetch WebSearch Task <DENY_READS> --permission-mode acceptEdits
```

| Flag                         | Effect                                                                                  |
|------------------------------|-----------------------------------------------------------------------------------------|
| `--restricted`               | no tools that run code, no WebFetch; ignores user/project/local settings; **file tools confined to the working directory** (the project folder) |
| `--tools …`                  | only these five built-in tools exist in the session                                    |
| `--strict-mcp-config`        | no MCP servers                                                                         |
| `--disable-slash-commands`   | no skills or slash commands                                                            |

This was checked against the real CLI: a `Read` of `/etc/hostname` is denied, while files in the folder can be read.
On top of it, `DENY_READS` explicitly denies reading `/proc` (the process environment holds the member's key), `/sys`,
`/etc`, the server's code and `.env`, `storage/.secrets/`, `storage/claude-home/` and the server account's `~/.claude`,
`~/.config`, `~/.ssh`, `~/.aws` and `~/.docker`; and each run's environment is rebuilt from scratch (§4).
The model check (`POST /api/models/check`) runs with `--restricted --tools ""` (no tools at all).

### 2. Prompt structure (`build_prompt`, `SECURITY_RULES`)

- The prompt puts the project name and description, earlier requests, the request, team notes and image paths in
  tagged blocks: `<project>`, `<earlier_requests>`, `<user_request>`, `<team_notes>`, `<reference_images>`.
- `clean_text()` removes control characters, defuses any of those tags found inside the text (`</team_notes>` →
  `‹/team_notes›`), and caps lengths (notes 600 characters, request 10000).
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

The server binds to `127.0.0.1` by default. To serve it publicly, put a reverse proxy with HTTPS in front and set
`TRUST_PROXY=true`, `COOKIE_SECURE=true` and `ATELIER_HOSTS=your.domain`.

**Known limit:** the browser check runs the generated pages' JavaScript in headless Chrome inside the app's container
(with `--no-sandbox` there). Running it, and the agents, in a separate locked-down runner container per job is the next
step for a public service.

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
| Change a limit                | Use the settings in `.env` (`MAX_BODY_MB`, `MAX_IMAGE_MB`, `MAX_EDIT_KB`, …)               |
| Change a plan's price or limits | Edit `PLANS` in `server/plans.py` (the pages read them from `/api/plans`)                |
| Change the shared agents      | Edit `agents/*.md`, then `manage.py make-agents-migration`                                  |

### Development notes

- **Tests:** `.venv/bin/python -m unittest discover -s tests -v`.
  - `tests/test_server.py` starts a server with a throwaway database, temporary `STORAGE_DIR` and `ATELIER_AGENTS`,
    `CLAUDE_BIN=tests/fake-claude`, `CLAUDE_KEYS=user` and `ACCOUNT_PURGE_SECONDS=1`. Test accounts save a test key
    and get the Agency plan unless a test asks otherwise.
  - The fake CLI behaves like the real one. Markers in the prompt pick the behaviour: `[[fail]]`, `[[fail-once]]`,
    `[[crash]]`, `[[hang]]`, `[[slow]]`, `[[escape]]`, `[[inject-script]]`, `[[redo]]`, `[[qr]]`, `[[pricey]]`,
    `[[orch:…]]`, `[[orch-form]]`, `[[orch-confirm]]`… A key containing `bad` or `broke` is refused like Claude does.
  - It logs every call to `$FAKE_CLAUDE_LOG` (flags, prompt, the last 4 characters of the key, `CLAUDE_CONFIG_DIR` and
    the names of the environment variables), so tests can check the exact flags, prompt and isolation.
- `STORAGE_DIR` and `ATELIER_AGENTS` move the data folders, for example for tests or a second instance.
- Run the server without `run.sh`: `cd server && ../.venv/bin/uvicorn main:app --reload --port 8001`.
- Logs go to the terminal (logger `agents-atelier`).
