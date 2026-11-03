# Agents Atelier

**Describe a web page. A team of specialist AI agents builds it in front of you, one after another.**

Agents Atelier is a local web app. You chat with a team of agents on the left, and the page they're building
appears on the right, reloading after every file an agent writes. Each agent is your own **Claude Code**
running with a different role: UX, Design System, Copywriter, HTML, CSS, Responsive, JS, Animation,
Accessibility, Critic, and more. There's no API key to set up; it uses your Claude Code login and plan.

The result is a plain HTML, CSS and JavaScript page you can download as a `.zip`, with no build step and no framework.

> Technical documentation (architecture, API, data formats, internals) is in **[doc.md](doc.md)**.

---

## What it does

- **A team, in order.** Agents work one after another, and each does a single job. They share one folder of files
  and a common plan (`plan.md`), so each picks up where the last one stopped.
- **You watch it happen.** Every agent posts a card in the chat showing the files it reads and edits, how long it
  took, and what it cost. The preview flashes and reloads after each change.
- **Projects you can come back to.** Each project has a name, a description, its own id, folder and chat history.
- **You stay in control:**
  - stop a run at any time, then **Continue** from where it stopped, or **Start again**
  - **retry** one agent with an extra instruction
  - switch agents on or off, or pick a **preset** team (Quick, Polish, Full…)
  - edit the code yourself in the **Code** tab
  - go back to any earlier **version**
- **Your agents are files.** Every agent is a Markdown file in `agents/`. Change its instructions, model, icon or
  order on the **Agents** page, or add your own.

## Requirements

- **Python 3.10+**
- **[Claude Code](https://claude.com/claude-code)**, installed and logged in: running `claude` in a terminal should work.
- A modern browser (Chrome, Firefox, Safari, Edge).

## Run it

```bash
./run.sh
```

Then open **<http://localhost:8001>**.

The first start creates a Python virtual environment in `.venv/` and installs FastAPI and Uvicorn. After that it
starts in a second. Stop the server with `Ctrl+C`.

### Settings

Set these in front of the command, for example `AGENT_MODEL=sonnet PORT=9000 ./run.sh`.

| Variable        | Default              | What it does                                                                     |
|-----------------|----------------------|----------------------------------------------------------------------------------|
| `PORT`          | `8001`               | Port the server listens on (it only listens on `127.0.0.1`, your own machine)    |
| `AGENT_MODEL`   | your Claude Code model | Model for agents that don't set their own `model:` (e.g. `sonnet`)            |
| `CLAUDE_BIN`    | `claude`             | Path to the Claude Code executable, if it isn't on your `PATH`                   |
| `AGENT_TIMEOUT` | `900`                | Seconds before a stuck agent is stopped (you can then press **Continue**)       |

## How to use it

1. **Create a project.** On the **Projects** page, click **New project**, then give it a name and a short description
   (both are required). Be specific: the agents read both with every prompt.
2. **Describe the page.** For example: *"A one-page site for Lumen, a bike repair shop in Lyon: hero with a booking
   button, services with prices, opening hours, map link."* Press **Enter**.
3. **Watch the team work.** The Team panel at the bottom shows who's working, who's done and who's next.
4. **Refine it.** Send follow-up prompts ("make the header calmer", "add a pricing section"); the team builds on the
   existing files. To save time, switch off agents you don't need or pick a preset first.
5. **Download it** with the **.zip** button.

### The pages

| Page         | Address        | What it's for                                                                      |
|--------------|----------------|------------------------------------------------------------------------------------|
| Home         | `/`            | What Agents Atelier is, the team, your recent projects                             |
| Projects     | `/projects`    | All projects: stats, search, filters, sort, grid or list view, pagination          |
| Studio       | `/p/<id>`      | One project: chat, live preview, code editor, team (opened from a project)         |
| Agents       | `/agents`      | View, edit, add, delete and reorder agents, plus the shared rules                  |
| Guide        | `/guide`       | A full tour of the app, tips for prompts, shortcuts and an FAQ                     |

### Inside the studio

| Where                    | What you can do                                                                                 |
|--------------------------|-------------------------------------------------------------------------------------------------|
| Chat                     | Prompt the team, attach reference images (paperclip, paste or drop), stop a run                 |
| Agent cards              | See each agent's steps, time and cost; **retry** one agent with an extra instruction            |
| After a stop or error    | **Continue from *agent*** (skips the finished agents) or **Start again**                        |
| Preview                  | Live page, desktop / tablet / phone widths, open in a new tab                                    |
| Code                     | Edit any file, with **Auto-save** on or off (Save button or Ctrl/Cmd+S); sizes and image previews |
| Versions (clock button)  | Every run and agent saves a version: open, compare, download or restore it                       |
| Cost & time (chart button) | Estimated cost, time and tokens per agent, for this project or all projects                   |
| Team panel               | Switch agents on or off, choose a preset, jump to an agent's card, see live progress            |

### Keyboard shortcuts

| Keys                     | Action                                       |
|--------------------------|----------------------------------------------|
| `Enter` / `Shift+Enter`  | Send the prompt / new line                   |
| `Ctrl+S`                 | Save the open file in the Code tab           |
| `Esc`                    | Close a menu, dialog or the projects drawer  |
| `/` and `N`              | Search projects / new project (Projects page) |
| `Alt+↑` / `Alt+↓`        | Move an agent up or down (Agents page)       |

## The agent team

The 10 agents switched on by default run in this order:

UX → Design System → Copywriter → HTML → CSS → Responsive → JS → Animation → Accessibility → Critic

The optional agents are **Brief**, **Assets**, **SEO**, **Performance** and **QA**.
Turn them on per project, or use a preset:

| Preset   | Agents                                         | Use it for                          |
|----------|------------------------------------------------|-------------------------------------|
| Default  | the 10 above                                   | A new page                          |
| Quick    | HTML, CSS                                      | Small changes, fastest and cheapest |
| Polish   | Accessibility, Performance, Critic             | Improving a page that already exists |
| Full     | all 15                                         | The most thorough result            |

You can also save your own presets from the Team panel.

The mechanical agents (HTML, CSS, Responsive, JS, Animation, Assets, SEO, Performance) run on the faster `sonnet`
model. The planning and judging agents use your default Claude Code model. You can change this per agent on the
**Agents** page.

## Good to know

- **Cost and time.** Each agent takes about 20–60 seconds, so the default team needs roughly 5–8 minutes. Every
  agent counts as one Claude Code session on your plan. The **Cost & time** panel shows an API-equivalent estimate;
  on a Claude subscription you aren't billed per call.
- **Safety.**
  - Agents can only read and write files inside their project folder. They can't run commands, browse the web or start other agents.
  - The server only listens on your machine (`127.0.0.1`) and has no login, so don't expose it to a network.
- **Your files** live in `work/<project-id>/`. You can open and edit them in any editor; the next prompt's agents
  read the current files first.
- **Limits:**
  - requests up to 8 MB
  - reference images up to 5 MB (PNG, JPG, WebP, GIF), up to 6 per prompt
  - files up to 500 KB in the Code tab
  - one agent run up to `AGENT_TIMEOUT`

## Troubleshooting

| You see                                         | What to do                                                                        |
|-------------------------------------------------|-----------------------------------------------------------------------------------|
| "Claude Code wasn't found"                      | Install Claude Code, or set `CLAUDE_BIN` to its path, then restart the server     |
| "Claude Code isn't logged in"                   | Run `claude` in a terminal, log in, then press **Continue**                       |
| "rate-limited or overloaded"                    | Wait a little, then press **Continue**                                            |
| "Can't reach the server"                        | Start it with `./run.sh` (or check the port)                                      |
| A banner saying the connection dropped          | The studio reconnects by itself; agents keep working on the server                |
| An agent "ran for more than N minutes"          | Press **Continue**, or raise `AGENT_TIMEOUT`                                      |
| An error with a **ref** code                    | Look for that ref in the server's terminal output for the full details            |

## Project layout

```text
.
├── README.md        this file
├── doc.md           technical documentation
├── run.sh           start the server
├── agents/          the agent team: one Markdown file per agent, shared rules, presets
├── server/          the Python server and the web pages
└── work/            your projects, their chat history and versions (created at first run)
```
