# Agents Atelier

**Describe a web page. A team of specialist AI agents builds it in front of you, one after another.**

Agents Atelier is a local web app that turns a short description into a finished web page, using a *team* of
AI agents instead of a single one. A UX agent plans the page, a Design System agent picks the colours and fonts,
a Copywriter writes the text, then HTML, CSS, Responsive, JS and Animation agents build it. An Accessibility agent
audits it, and a Critic reviews and scores the result.

You watch every step live. You can stop, steer, retry or roll back any of them, and at the end you download a plain
HTML, CSS and JavaScript page that's yours.

Every agent is your own **Claude Code** running with a different role, so there's no API key and no account to
create. Everything runs on your machine.

> Technical documentation (architecture, API, data formats, internals) is in **[doc.md](doc.md)**.

## Contents

- [Why Agents Atelier](#why-agents-atelier)
- [How it works](#how-it-works)
- [Getting started](#getting-started)
- [Using it](#using-it)
- [The agent team](#the-agent-team)
- [Customising the agents](#customising-the-agents)
- [Good to know](#good-to-know)
- [FAQ](#faq)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)

---

## Why Agents Atelier

Most AI website builders are a single assistant behind a chat box. You ask, wait, and get a result you can only
accept or re-roll. Agents Atelier works more like a small design studio:

| A single AI builder                         | Agents Atelier                                                                 |
|---------------------------------------------|--------------------------------------------------------------------------------|
| One generalist does everything at once      | **15 specialists**, each with one job and its own instructions                 |
| You see the result, not the process         | You see **every agent, every file it touches, how long it took and its cost**  |
| Unhappy? Regenerate everything              | **Retry just one agent** ("CSS: warmer palette") or **roll back** to any step |
| Stopping loses the work                     | **Stop, then Continue** from where it stopped, even after a crash              |
| Fixed behaviour                             | Every agent is a **Markdown file you can edit**, with its own model            |
| Hosted, often framework code                | **Runs locally**, outputs plain HTML/CSS/JS with **no build step**             |

It's a good fit for landing pages, portfolios, product pages, event pages, prototypes, and for learning how a page is
built step by step by reading what each specialist did.

## How it works

```text
            you: "A one-page site for Lumen, a bike repair shop in Lyon…"
                                        │
   ┌──────────────┐   ┌──────────────┐  │  ┌──────────────┐        ┌──────────────┐
   │  UX          │ → │ Design System│ →│→ │  Copywriter  │ → … →  │  Critic      │
   │ plan.md      │   │ :root tokens │     │ all the text │        │ fixes + score│
   └──────────────┘   └──────────────┘     └──────────────┘        └──────────────┘
          │                  │                    │                        │
          └──────────────────┴──── one shared project folder ──────────────┘
                     index.html · style.css · script.js · plan.md
                                        │
                     live preview reloads after every file written
```

1. **One project, one folder.** Each project has its own folder of files: `index.html`, `style.css`, `script.js`
   and `plan.md`. The agents work on these files directly.
2. **Agents run in order.** Each agent is a separate Claude Code session with a role prompt. It reads the current
   files, does only its own job, and hands over a one-line note to the agents after it.
3. **`plan.md` is the shared memory.** The UX agent writes the structure, Design System the tokens, the Copywriter
   the text, and so on. Each agent owns one section, so later agents build on earlier decisions instead of guessing.
4. **Small edits, never overwrite.** The shared rules tell agents to change only what their job needs, so the CSS
   agent doesn't undo the HTML agent's work.
5. **You see it live.** Every file read or written appears on the agent's card. The preview reloads after each
   change, and a version of the code is saved after each agent.

### What happens when you press Enter

A typical run of the default team (times vary):

| Time   | Agent          | What it does                                                                 |
|--------|----------------|------------------------------------------------------------------------------|
| 0:00   | —              | A version "Before: …" is saved                                               |
| 0:00   | UX             | Writes `## Structure` in `plan.md`: sections, navigation, calls to action    |
| 0:30   | Design System  | Puts colours, fonts, type scale and spacing as CSS variables in `style.css`  |
| 1:00   | Copywriter     | Writes every headline, button label and form message                         |
| 1:40   | HTML           | Builds `index.html` from the plan and the copy (the preview appears)         |
| 2:10   | CSS            | Styles everything with the tokens                                            |
| 2:50   | Responsive     | Makes it work from 320px phones to wide screens                              |
| 3:20   | JS             | Adds the menu, forms and smooth scrolling                                    |
| 3:50   | Animation      | Adds scroll reveals and micro-interactions                                   |
| 4:20   | Accessibility  | Fixes contrast, focus, labels and headings (WCAG 2.2 AA)                     |
| 5:00   | Critic         | Fixes the three biggest issues and scores the page out of 10                 |

### What every agent knows

Each agent receives:
- the **project's name and description**, which is why every project must have both
- the **current request**, plus the last few requests in the project
- the **notes from the agents before it**, for example "CSS agent: styled the hero; the menu button uses `data-menu-toggle`"
- any **reference images** you attached
- the **shared rules** (`agents/_common.md`) followed by **its own role** (`agents/NN-name.md`)

Agents can only use file tools (read, write, edit, search) inside the project folder. They can't run commands,
browse the web or start other agents.

## Getting started

### Requirements

- **Python 3.10+**
- **[Claude Code](https://claude.com/claude-code)**, installed and logged in: running `claude` in a terminal should work.
- A modern browser (Chrome, Firefox, Safari, Edge).

### Run it

```bash
./run.sh
```

Then open **<http://localhost:8001>**.

The first start creates a Python virtual environment in `.venv/` and installs FastAPI and Uvicorn. After that it
starts in a second. Stop the server with `Ctrl+C`.

### Settings

Set these in front of the command, for example `AGENT_MODEL=sonnet PORT=9000 ./run.sh`.

| Variable        | Default                | What it does                                                                  |
|-----------------|------------------------|-------------------------------------------------------------------------------|
| `PORT`          | `8001`                 | Port the server listens on (only on `127.0.0.1`, your own machine)            |
| `AGENT_MODEL`   | your Claude Code model | Model for agents that don't set their own `model:` (e.g. `sonnet`)            |
| `CLAUDE_BIN`    | `claude`               | Path to the Claude Code executable, if it isn't on your `PATH`                |
| `AGENT_TIMEOUT` | `900`                  | Seconds before a stuck agent is stopped (you can then press **Continue**)     |

## Using it

### Your first page in five steps

1. **Create a project.** On the **Projects** page, click **New project**, then give it a name and a description.
   Both are required, and the agents read them with every prompt. Example: *"Lumen Bikes — a repair shop in Lyon.
   One page that makes people book a repair: services with prices, opening hours, location."*
2. **Describe the page.** *"A one-page site with a hero and a booking button, services with prices, opening hours
   and a map link. Calm, trustworthy, a bit playful."* You can attach a sketch or screenshot with the paperclip, or
   paste or drop it. Press **Enter**.
3. **Watch the team.** Agent cards appear in the chat. The Team panel at the bottom shows who's working, who's done
   and who's next, and the preview fills in as files are written.
4. **Refine.** Ask for changes: *"make the hero image larger"*, *"add a FAQ section"*. The team builds on the
   existing files. For small changes, pick the **Quick** preset (HTML and CSS only) to save time.
5. **Download** the result with the **.zip** button. It's plain HTML, CSS and JS, so put it on any host.

### The pages

| Page     | Address     | What it's for                                                                 |
|----------|-------------|-------------------------------------------------------------------------------|
| Home     | `/`         | What Agents Atelier is, its features, the team, your recent projects          |
| Projects | `/projects` | All projects: stats, search, filters, sort, grid or list view, pagination     |
| Studio   | `/p/<id>`   | One project: chat, live preview, code editor, team (opened from a project)    |
| Agents   | `/agents`   | List and reorder agents; `/agents/<id>` to view one, `/agents/new` to create, `/agents/<id>/edit`, `/agents/rules` |
| Teams    | `/teams`    | List teams; `/teams/<id>` to view one, `/teams/new` to create, `/teams/<id>/edit` |
| Guide    | `/guide`    | A full tour, tips for prompts, shortcuts and an FAQ                           |

### Inside the studio

| Where                      | What you can do                                                                          |
|----------------------------|------------------------------------------------------------------------------------------|
| Chat                       | Prompt the team, attach reference images, stop a run                                     |
| Agent cards                | See each agent's steps, time and cost; **retry** that agent with an extra instruction   |
| After a stop or an error   | **Continue** from the stopped agent (finished ones are skipped) or **Start again**       |
| Preview                    | The live page at desktop, tablet or phone width, or open it in a new tab                 |
| Code                       | Edit any file, with **Auto-save** on or off (Save button or Ctrl/Cmd+S)                  |
| Versions (clock button)    | A version after every agent: open, compare line by line, download or restore            |
| Cost & time (chart button) | Estimated cost, time and tokens per agent, for this project or all projects              |
| Team panel                 | Switch agents on or off, choose a preset, jump to an agent's card, follow progress       |

### Keyboard shortcuts

| Keys                    | Action                                         |
|-------------------------|------------------------------------------------|
| `Enter` / `Shift+Enter` | Send the prompt / new line                     |
| `Ctrl+S`                | Save the open file in the Code tab             |
| `Esc`                   | Close a menu, dialog or the projects drawer    |
| `/` and `N`             | Search projects / new project (Projects page)  |
| `Alt+↑` / `Alt+↓`       | Move an agent up or down (Agents page)         |

## The agent team

| #  | Agent          | Default | Model   | What it does                                                                   |
|----|----------------|---------|---------|--------------------------------------------------------------------------------|
| 01 | Brief          | off     | default | Turns your prompt into a brief: goal, audience, tone, success criteria         |
| 02 | UX             | on      | default | Plans the structure: sections, navigation, user flow, calls to action          |
| 03 | Design System  | on      | default | Defines palette, fonts, type scale, spacing and radii as CSS variables         |
| 04 | Copywriter     | on      | default | Writes every word: headlines, body text, buttons, form labels and errors       |
| 05 | HTML           | on      | sonnet  | Builds semantic, accessible markup from the plan and the copy                  |
| 06 | CSS            | on      | sonnet  | Styles everything with the tokens: layout, hierarchy, states, mobile first    |
| 07 | Assets         | off     | sonnet  | Draws the logo, favicon, icons and illustrations as hand-written SVG           |
| 08 | Responsive     | on      | sonnet  | Makes it work from 320px phones to wide desktops                               |
| 09 | JS             | on      | sonnet  | Adds interactivity in vanilla JavaScript: menu, tabs, forms, scrolling         |
| 10 | Animation      | on      | sonnet  | Adds tasteful motion, respecting reduced-motion settings                       |
| 11 | Accessibility  | on      | default | Audits against WCAG 2.2 AA and fixes what it finds                             |
| 12 | SEO            | off     | sonnet  | Title, meta description, Open Graph tags, structured data                      |
| 13 | Performance    | off     | sonnet  | Leaner CSS and JS, smart loading, no layout shift                              |
| 14 | QA             | off     | default | Traces links, buttons, forms and selectors like a tester, and fixes bugs       |
| 15 | Critic         | on      | default | Reviews like a senior designer and engineer, fixes the top issues, scores it   |

"default" means your Claude Code model (or `AGENT_MODEL`). The mechanical agents use the faster `sonnet`, while the
planning and judging agents use the stronger default.

### Teams

A **team** is a set of agents that run together, always in the agents' order. Pick one in a project's Team panel, or
manage them on the **Teams** page: create your own, view a team's agents with estimated time and cost, edit, duplicate
or delete it. The four built-in teams can be viewed and duplicated, but not changed.

| Team    | Agents                             | Use it for                              |
|---------|------------------------------------|-----------------------------------------|
| Default | the 10 agents marked "on"          | A new page                              |
| Quick   | HTML, CSS                          | Small changes, fastest and cheapest     |
| Polish  | Accessibility, Performance, Critic | Improving a page that already exists    |
| Full    | all 15                             | The most thorough result                |

In the studio, choose a team from the Team panel, or save your current selection as a new team.

## Customising the agents

Every agent is a Markdown file in `agents/`: a short header, then its instructions.

```markdown
---
id: dark-mode
name: Dark mode
icon: eye
color: "#334455"
default: false
model: sonnet
description: Adds a dark theme that follows the visitor's system setting.
---
You are the Dark mode agent. Add a dark theme using prefers-color-scheme,
reusing the CSS variables in :root. Don't change the light theme.
```

- **Create, view and edit agents** on the Agents pages (`/agents`, `/agents/new`, `/agents/<id>`), or in any text
  editor. Changes apply to the next run, with no restart needed.
- **Models come from a list, not free text.** The list holds the aliases your Claude CLI advertises
  (`fable`, `opus`, `sonnet`, read from `claude --help`), the current full model names (`claude-opus-5-5`,
  `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-fable-5-1`), and any model you add with **Another model… → Check
  with Claude CLI**. That check makes one tiny call, and only models the CLI accepts are saved. An agent whose
  file names an unknown model gets a warning on its page and in the studio, and fails at once with a clear message.
- **The number in the file name sets the order.** Drag agents on the Agents page to reorder them.
- **`agents/_common.md`** holds the shared rules every agent follows. Edit it to change what all agents know.
- Full details are in [doc.md](doc.md#3-agents).

## Good to know

- **Time and cost:**
  - Each agent takes about 20–60 seconds, so the default team needs roughly 5–8 minutes.
  - Every agent run is one Claude Code session on your plan.
  - The Cost & time panel shows an API-equivalent estimate. On a Claude subscription you aren't billed per call.
- **Nothing is lost.** A version is saved before each run and after every agent, and restoring one saves the current
  code first. Stopped runs can be continued, even after a server restart.
- **Your files are yours.** They live in `work/<project-id>/`, so you can open and edit them in any editor. The next
  prompt's agents read the current files first.
- **Safety and prompt injection.** Agents read text you didn't write yourself: earlier agents' notes, `plan.md`,
  the page, text inside images. So they're treated as untrusted:
  - **Locked to the project folder.** Each agent runs Claude Code in `--restricted` mode with only five file tools
    (read, write, edit, search). It has no shell, no web, no MCP servers, and none of your personal Claude settings.
    Reads and writes outside the project folder are blocked by the CLI itself.
  - **Data is marked as data.** Project details, notes and image paths are passed in labelled blocks and cleaned so
    they can't fake their way out. Every agent also gets security rules that the server adds last and that can't be
    edited away.
  - **You're told when something's off.** A blocked attempt (for example reading `/etc/passwd`), or a page that
    starts loading scripts, iframes or data from another site, shows a **Security check** card in the chat.
  - **Generated pages are sandboxed.** In the preview, in thumbnails or in their own tab, they run in an isolated
    origin, so their JavaScript can't use the app's API.
  - **Only your own browser tabs can make changes.** The server only answers on `localhost`, refuses changes coming
    from other websites, and has no login, so don't expose it to a network.
- **Limits:**
  - requests up to 8 MB
  - reference images up to 5 MB each (PNG, JPG, WebP, GIF), up to 6 per prompt
  - files up to 500 KB in the Code tab
  - one agent run up to `AGENT_TIMEOUT`

## FAQ

**Do I need an API key?**
No. Agents Atelier runs your installed Claude Code, which uses your existing login and plan.

**What does it produce?**
A single page: `index.html`, `style.css`, `script.js`, plus SVG assets if the Assets agent is on. It uses no
framework and needs no build step, so it works on any static host.

**Can it build multi-page sites or apps with a backend?**
Not yet. The agents' shared rules target one page of plain HTML/CSS/JS. You can change those rules in
`agents/_common.md` if you want to experiment.

**Why is it slower than single-AI builders?**
Ten specialists each do a careful pass. For small changes, use the **Quick** preset or switch agents off. You can
also start the server with `AGENT_MODEL=sonnet` for faster runs.

**What if I don't like what one agent did?**
Click the retry button on its card and add an instruction, or open **Versions** and restore the code from before
that agent ran.

**Can I edit the code myself?**
Yes, in the Code tab or in any editor. The agents read the current files on the next prompt.

**Does it work offline?**
The app does, but the agents need Claude Code to reach Claude.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The suite (`tests/test_server.py`, about 40 tests in roughly 15 seconds) starts its own server on a free port, with a
temporary copy of `agents/` and an empty `work/` folder, so your data is never touched. It uses a **fake Claude CLI**
(`tests/fake-claude`), so no model is called and nothing is spent. It covers:
- every page and API error path
- request guards (foreign Host, cross-site writes, body size)
- files, uploads and versions
- agents, models and teams
- WebSocket messages
- full runs: stop / continue / start again / retry, timeouts, CLI errors
- the prompt-injection defences: tool isolation, data blocks, blocked actions and the page scan

Set `KEEP_TEST_FILES=1` to keep the temporary folder for inspection.

## Troubleshooting

| You see                                | What to do                                                                     |
|----------------------------------------|--------------------------------------------------------------------------------|
| "Claude Code wasn't found"             | Install Claude Code, or set `CLAUDE_BIN` to its path, then restart the server  |
| "Claude Code isn't logged in"          | Run `claude` in a terminal, log in, then press **Continue**                    |
| "rate-limited or overloaded"           | Wait a little, then press **Continue**                                         |
| "Can't reach the server"               | Start it with `./run.sh`, or check the port                                    |
| A banner saying the connection dropped | The studio reconnects by itself; agents keep working on the server            |
| An agent "ran for more than N minutes" | Press **Continue**, or raise `AGENT_TIMEOUT`                                   |
| A **Security check** card in the chat  | Read what was blocked or found, check the files, restore a version if needed   |
| "Unknown host" or "other websites"     | Open the app at `http://localhost:<port>` (set `ATELIER_HOSTS` to allow another name) |
| An error with a **ref** code           | Search the server's terminal output for that ref to see the full details       |

## Project layout

```text
.
├── README.md        this file
├── doc.md           technical documentation
├── run.sh           start the server
├── agents/          the agent team: one Markdown file per agent, shared rules, saved presets
├── server/          the Python server (FastAPI) and the web pages
├── tests/           automated tests and a fake Claude CLI
└── work/            your projects, chat history and versions (created at first run, not in git)
```
