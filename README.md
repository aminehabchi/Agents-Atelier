# Agents Atelier

**Hire AI agents, not freelancers. Describe the website you need, and a team of specialist AI agents builds it in
front of you, one after another.**

Agents Atelier works like a freelance platform where every specialist is an AI agent. Instead of writing a job post,
comparing offers and waiting days for a delivery, you describe the job and hire a team that starts at once: a UX agent
plans the page, a Design System agent picks the colours and fonts, a Copywriter writes the text, then HTML, CSS,
Responsive, JS and Animation agents build it. An Accessibility agent audits it, and a Critic reviews and scores the
result in a real browser, sending bigger problems back to the agent that owns them.

| On a freelance platform                 | On Agents Atelier                                                       |
|-----------------------------------------|-------------------------------------------------------------------------|
| Browse profiles and hire people         | Browse **30 specialist agents** with profiles, ratings and portfolios  |
| Hire an agency for a whole job          | Hire one of **22 ready teams** (restaurant site, portfolio, 3D site, redesign…), each with a starting prompt |
| Offers, then days of waiting             | **Start a job**: pick one of your projects and the team starts at once  |
| Each round of changes means new messages | Ask for a change anytime: **only the agents it needs** work on it      |
| You trust a profile                     | Every agent's **instructions are readable**, and its **time and cost** are shown |
| Build your own agency                   | **Duplicate any agent**, change its instructions, save **your own teams** |

The work itself is **front end only**: the team builds a finished static website (one page or several) in plain HTML,
CSS and JavaScript that you download and host anywhere.

### The whole idea in one paragraph

Agents Atelier is a web platform that works like a freelance marketplace, except that every specialist you hire is an
AI agent: rather than posting a job, comparing offers and waiting days for a delivery, you describe the website you
need and hire a team of agents that starts working the moment you press Enter. Each project begins with a name, a short
description and optional answers about your audience, goal, tone, colours, languages and must-have content; you can
give it its own brand kit (logo, colours, fonts), or import an existing site to improve. You then pick one of 22
ready use-case teams (a restaurant site, a portfolio, a landing page, a redesign, a multilingual version, legal
pages, a 3D site…), each with a starting prompt to fill in, or choose agents yourself from 30 specialists: a Brief agent turns your
request into a clear brief, UX plans the structure, Design System sets the colours and fonts, the Copywriter writes
every word, HTML, CSS, JS, Responsive and Animation agents build the pages, Accessibility audits them, and QA and the
Critic check the result in a real browser at desktop and phone width, fix small problems and send bigger ones back to
the agent responsible. You watch all of it live in the studio, with each agent's steps, time and cost, a preview that
reloads after every edit, a code editor and a version saved after every agent, and after the first build an
Orchestrator runs only the agents each follow-up needs. You can stop, continue, retry a single agent, or duplicate any
agent and edit its Markdown instructions to build your own teams. The result is plain HTML, CSS and JavaScript you
download as a .zip and host anywhere, inside a platform with real accounts, admin analytics, privacy tools and an
interface in English, French or Moroccan Darija.

You watch every step live. You can stop, steer, retry or roll back any of them, and at the end you download plain
HTML, CSS and JavaScript files that are yours.

> **Front end only.** The agents write the part of a site that runs in the browser: layout, styles, copy, images
> (SVG), interactions and animations. They don't build a back end: no server code, database, user accounts,
> payments or APIs. Forms, logins and data in the result are front-end mock-ups; connect them to a real back end
> yourself if you need one.

Every agent is **Claude Code** running with a different role, so there's no API key. People sign up with an email and
a password; accounts, agents and teams live in Postgres. The app speaks **English, French and Moroccan Darija**
(right to left), in light or dark.

![The Agents Atelier studio: chat with the agents on the left, live preview of the page being built on the right, and the agent team below](docs/screenshots/studio.png)

*The studio: talk to the team on the left, watch the page being built on the right, and see each agent's step in the
team strip below. (Light theme: [docs/screenshots/studio-light.png](docs/screenshots/studio-light.png).)*

> Technical documentation (architecture, API, data formats, internals) is in **[doc.md](doc.md)**.

## Contents

- [Why Agents Atelier](#why-agents-atelier)
- [How it works](#how-it-works)
- [Getting started](#getting-started)
- [Using it](#using-it)
- [The agent team](#the-agent-team)
- [The marketplace](#the-marketplace)
- [Customising the agents](#customising-the-agents)
- [Sharing agents and teams](#sharing-agents-and-teams)
- [Languages, theme and colours](#languages-theme-and-colours)
- [Accounts, privacy and protection](#accounts-privacy-and-protection)
- [Good to know](#good-to-know)
- [FAQ](#faq)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)

---

## Why Agents Atelier

Most AI website builders are a single assistant behind a chat box. You ask, wait, and get a result you can only
accept or re-roll. Agents Atelier works more like a small design studio:

| A single AI builder                         | Agents Atelier                                                                 |
|---------------------------------------------|--------------------------------------------------------------------------------|
| One generalist does everything at once      | **30 specialists**, each with one job and its own instructions                 |
| A blank chat box                            | **22 use-case teams**, each with the right agents and a prompt to fill in      |
| You see the result, not the process         | You see **every agent, every file it touches, how long it took and its cost**  |
| Unhappy? Regenerate everything              | **Retry just one agent** ("CSS: warmer palette") or **roll back** to any step |
| Stopping loses the work                     | **Stop, then Continue** from where it stopped, even after a crash              |
| Nobody looks at the page                    | QA and the Critic get **real browser checks** at desktop and phone width       |
| Fixed behaviour                             | Every agent is a **Markdown file you can edit**, with its own model            |
| Hosted, often framework code                | Outputs plain HTML/CSS/JS with **no build step**, hosted anywhere              |

It's a good fit for landing pages, portfolios, restaurant and local business sites, product and event pages, small
brochure sites, UI prototypes and mock-ups to hand to a back-end developer. It's not meant for web apps that need a
server, a database or logins.

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

**Who works is decided first:**

- **First prompt on a new project:** every agent that's switched on builds the site.
- **Any later prompt:** the **Orchestrator** reads your request and the project (files, `plan.md`, earlier requests, the
  last run) and picks only the agents the request needs. "Make the buttons rounder" runs CSS, not the whole team. Its
  choice and reason appear in the chat, and the run starts right away (press Stop if it picked wrong). It may also pick
  a helper that's switched off when the request clearly needs it ("translate the site into Arabic" brings in the
  Translator).
- **The Brief agent always runs** whenever any agent runs, so its switch is locked on. On follow-ups it restates your
  request as a precise change instead of rewriting the brief.
- The server adds what the Orchestrator might forget: agents that must adapt (HTML changes → CSS) and **QA + Critic
  after any code change**.
- **Unclear, off-topic or a question** ("fix it", "write me a poem", "which fonts did you use?"): no agent runs. The
  Orchestrator answers or asks one question in the chat; just reply.
- If the Orchestrator fails or gives an unusable answer, every switched-on agent runs, so a prompt is never blocked.

The Orchestrator's instructions are in `agents/_orchestrator.md`.

A typical first build with the default team (times vary):

| Time   | Agent          | What it does                                                                 |
|--------|----------------|------------------------------------------------------------------------------|
| 0:00   | (server)       | A version "Before: …" is saved                                               |
| 0:00   | Brief          | Turns your prompt into a brief: goal, audience, tone, success criteria       |
| 0:20   | UX             | Writes `## Structure` in `plan.md`: sections, navigation, calls to action    |
| 0:50   | Design System  | Puts colours, fonts, type scale and spacing as CSS variables in `style.css`  |
| 1:20   | Copywriter     | Writes every headline, button label and form message                         |
| 2:00   | HTML           | Builds `index.html` from the plan and the copy (the preview appears)         |
| 2:30   | CSS            | Styles everything with the tokens                                            |
| 3:10   | JS             | Adds the menu, forms and smooth scrolling                                    |
| 3:40   | Responsive     | Makes it work from 320px phones to wide screens                              |
| 4:10   | Animation      | Adds scroll reveals and micro-interactions                                   |
| 4:40   | Accessibility  | Fixes contrast, focus, labels and headings (WCAG 2.2 AA)                     |
| 5:20   | Critic         | Gets a browser check, fixes small issues, sends big ones back (up to 2 rounds), scores out of 10 |

### What every agent knows

Each agent receives:
- the **project's name and description**, and the answers to the optional project questions (audience, goal, tone,
  colours, languages, must-have content)
- the project's **brand kit** (logo, colours, heading and body fonts) in a `<brand_kit>` block
- the **current request**, plus the last few requests in the project
- the **notes from the agents before it**, for example "CSS agent: styled the hero; the menu button uses `data-menu-toggle`"
- any **reference images** you attached
- for QA and the Critic: a **browser check** (see below)
- the **shared rules** (`agents/_common.md`) followed by **its own role**, then security rules the server adds last
- an instruction to write its chat messages in **your interface language** (French or Darija); the site itself keeps
  following the brief

Agents can only use file tools (read, write, edit, search) inside the project folder. They can't run commands or start
other agents. Only agents marked for web research (Research) can search the web.

### Real browser checks

Agents have no browser of their own, so before **QA** and the **Critic** work, the server opens the site in headless
Chrome at desktop and phone width and hands over what it saw: console errors, files that failed to load, broken images,
links to missing pages or anchors, and sideways overflow on a phone. Screenshots go to the project's `.checks/` folder
(left out of downloads and versions) for the agents to look at, and a summary card with thumbnails appears in the chat.
Turn it off with `BROWSER_CHECKS=false`; set `CHROME_BIN` if Chrome isn't found.

## Getting started

### Requirements

- **Python 3.10+**
- **Docker** (for Postgres), or any Postgres 14+ you point `DATABASE_URL` at
- **[Claude Code](https://claude.com/claude-code)**, installed and logged in: running `claude` in a terminal should work.
- **Google Chrome or Chromium** (optional) for the real browser checks
- A modern browser (Chrome, Firefox, Safari, Edge).

### Run it

```bash
cp .env.example .env      # then set ADMIN_EMAIL (and SMTP_* to send real email)
./run.sh
```

Then open **<http://localhost:8001>**.

The first start creates a Python virtual environment in `.venv/`, installs FastAPI, Uvicorn and asyncpg, starts
Postgres with Docker (`database/docker-compose.yaml`) and applies the database migrations. After that it starts in a
second. Stop the server with `Ctrl+C`; Postgres keeps running until you run
`docker compose -f database/docker-compose.yaml down`.

### Your admin account

1. In `.env`, set `ADMIN_EMAIL` to your email, then start the server.
2. Open **/register** and create your account with that email, accepting the terms.
3. Enter the 6-digit code from the email. **Without `SMTP_HOST` no email is sent: the code is in the server log.**
4. You're the admin. `server/manage.py make-admin <email>` promotes another account later.

| Role          | Can do                                                                                                   |
|---------------|----------------------------------------------------------------------------------------------------------|
| Not confirmed | Only enter the code from the email (or ask for a new one) and log out                                    |
| User          | Their own projects; sees the shared agents and teams, duplicates them, and creates, edits and deletes their own |
| Admin         | Everything a user can, plus: changes the shared agents, teams and rules, opens every project, manages users on **/users**, sees **/analytics** |

A user's agents and teams are private to them. Visitors who aren't logged in only see the landing page, the login and
sign-up pages and the legal pages.

### Settings

Settings live in a **`.env`** file at the project root; `.env.example` documents every one. A variable set on the
command line wins over the file: `AGENT_MODEL=sonnet PORT=9000 ./run.sh`. Restart the server after changing `.env`.

| Variable | Default | What it does |
|---|---|---|
| **Server** | | |
| `HOST` / `PORT` | `127.0.0.1` / `8001` | Address and port the server listens on |
| `ATELIER_HOSTS` | *(empty)* | Extra host names allowed to reach the server, comma-separated |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `ATELIER_WORK` / `ATELIER_AGENTS` | `work` / `agents` | Folders for projects and for the agent files |
| **Claude Code** | | |
| `CLAUDE_BIN` | `claude` | Path to the Claude Code executable |
| `AGENT_MODEL` | your Claude Code model | Model for agents that don't set their own (e.g. `sonnet`) |
| `AGENT_TIMEOUT` | `900` | Seconds before a stuck agent is stopped (you can then press **Continue**) |
| `REVIEW_ROUNDS` | `2` | Times the Critic may send work back in one run |
| `ATELIER_PLAN` | `pro` | `free`: the Critic reviews once and never sends work back |
| **Limits** | | |
| `MAX_BODY_MB` / `MAX_IMAGE_MB` | `8` / `5` | Largest request / reference image |
| `MAX_SITE_IMPORT_MB` | `40` | Largest existing site a project can start from |
| `MAX_EDIT_KB` | `500` | Largest file the Code tab opens or saves |
| `MAX_VERSIONS` | `150` | Versions kept per project (the oldest go first) |
| `MAX_ASSETS` | `12` | Logo and photos a new project can be given |
| **Database and accounts** | | |
| `DATABASE_URL` | local Docker Postgres | Where accounts, agents and teams live |
| `ADMIN_EMAIL` | *(empty)* | This account becomes admin once confirmed |
| `SESSION_DAYS` | `30` | Days a login stays valid |
| `COOKIE_SECURE` | `false` | Set `true` when served over HTTPS |
| `CODE_MINUTES` | `15` | Minutes an email code stays valid |
| **Sign-up protection** | | |
| `TRUST_PROXY` | `false` | Behind nginx/Caddy, use the visitor's address from `X-Forwarded-For` |
| `SIGNUPS_PER_HOUR` / `LOGINS_PER_15_MIN` / `RESETS_PER_HOUR` | `5` / `30` / `10` | Limits per IP address (`0` = none) |
| `BLOCK_DISPOSABLE_EMAILS` / `DISPOSABLE_DOMAINS_FILE` | `true` / *(empty)* | Refuse throwaway inboxes; add your own domain list |
| **Browser checks** | | |
| `BROWSER_CHECKS` / `CHROME_BIN` | `true` / *(found automatically)* | Real browser checks for QA and the Critic |
| **Legal pages** | | |
| `LEGAL_NAME` / `LEGAL_EMAIL` / `LEGAL_ADDRESS` | *(empty)* | Who runs the service, shown on /terms and /privacy. Fill in before going public |
| **Email** | | |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_SECURITY` | no email sent | Without `SMTP_HOST`, codes are printed in the server log |
| `EMAIL_OUTBOX` | *(empty)* | Also write every email to this file, one JSON object per line |

## Using it

### Your first job in five steps

1. **Describe the job.** Click **New project** (the page `/projects/new`). Give it a name and a short description,
   like a job post; the agents read both with every prompt. The optional questions (kind of site, audience, goal, tone,
   languages, must-have content, sites you like, things to avoid) help the first build land closer. Add your logo and
   photos, fill in the project's **brand kit** (logo, up to six colours, heading and body fonts, or copy another
   project's kit), or start from an **existing site** (a folder or a .zip with an `index.html`).
2. **Hire a team.** The studio offers the 22 ready teams: pick one and its agents are switched
   on and its starting prompt is filled in; replace the parts in [brackets]. Or write your own: *"A one-page site with
   a hero and a booking button, services with prices, opening hours and a map link. Calm, trustworthy."* Attach a
   sketch or screenshot with the paperclip, or paste or drop it. Press **Enter**.
3. **Watch the team work.** Agent cards appear in the chat. The Team panel at the bottom shows who's working, who's done
   and who's next, and the preview fills in as files are written.
4. **Ask for changes.** One change at a time: *"make the hero image larger"*, *"add a FAQ section"*. The Orchestrator
   runs only the agents that change needs, on the existing files.
5. **Take the work:** download the result with the **.zip** button. It's plain HTML, CSS and JS, so put it on any host.

The **Guide** (`/guide`, in the account menu) has a full tour, a prompt recipe (what, who, goal, content, style) and
tips for good results.

### The pages

| Page       | Address          | What it's for                                                                 |
|------------|------------------|-------------------------------------------------------------------------------|
| Landing    | `/`              | For visitors: hire AI agents, not freelancers; examples, use cases, features, FAQ |
| Dashboard  | `/`              | Once logged in: your numbers, runs over 14 days, time per agent, recent work  |
| Hire       | `/hire`          | The marketplace: agents and teams by category, search, sort, Start a job       |
| Profiles   | `/hire/agents/<n>`, `/hire/teams/<n>` | Jobs, time, cost, Critic score, ratings, portfolio, instructions |
| Projects   | `/projects`      | Your projects: stats, search, filters, sort, grid or list view, pagination    |
| New project| `/projects/new`  | Every project field on one screen                                             |
| Studio     | `/p/<id>`        | One project: chat, live preview, code, versions, cost, brand kit, team, ratings |
| My agents  | `/agents`        | Mine or shared, on or optional; view, create, edit, reorder, publish, export  |
| My teams   | `/teams`         | Mine or shared use-case teams with their starting prompts; publish           |
| Guide      | `/guide`         | Tour, prompt recipe, tips, shortcuts and FAQ (logged in)                      |
| Profile    | `/profile`       | Language, theme, details, password, devices, your data, delete                |
| Users      | `/users`         | Admins: every account in a paginated table, change roles                      |
| Analytics  | `/analytics`     | Admins: sign-ups, active users, runs, cost per day, top accounts              |
| Legal      | `/terms`, `/privacy` | Terms of use and privacy policy                                           |
| Account    | `/login`, `/register`, `/forgot` | Log in, sign up (with terms), reset a password by 6-digit code |

### Inside the studio

| Where                      | What you can do                                                                          |
|----------------------------|------------------------------------------------------------------------------------------|
| Chat                       | Prompt the team, start from a use case, attach reference images, stop a run              |
| Agent cards                | See each agent's steps, time and cost; **retry** that agent with an extra instruction   |
| Browser check cards        | What the real browser saw, with desktop and phone thumbnails                             |
| After a stop or an error   | **Continue** from the stopped agent (finished ones are skipped) or **Start again**       |
| Preview                    | The live page at desktop, tablet or phone width, a page switcher for multi-page sites, or open it in a new tab |
| Code                       | Edit any file, with **Auto-save** on or off (Save button or Ctrl/Cmd+S)                  |
| Research                   | The Research agent's sourced report, when it ran                                         |
| Versions (clock button)    | A version after every agent: open, compare line by line, download or restore            |
| Cost & time (chart button) | Estimated cost, time and tokens per agent, for this project or all projects              |
| Team panel                 | Switch agents on or off, choose a team, save your selection as a team, jump to a card   |

### Multi-page sites

Give a project several pages (Home, About, Contact…) when you create it, or later under **Edit name, description
and pages**. The first page is always `index.html`; the others get file names from their titles (`about.html`,
`contact.html`). Every agent receives the page list, and the HTML agent builds every page with the same header,
navigation and footer and a shared `style.css` / `script.js`. The page switcher above the preview shows any page.

### Command palette and keyboard shortcuts

Press **Ctrl+K** (⌘K on a Mac), or the search button in the header, on any page: jump to any project, agent, team or
page, or run an action (new project, import or export, theme, and in the studio: versions, cost & time, download, stop,
continue, switch view, device or page).

| Keys                    | Action                                         |
|-------------------------|------------------------------------------------|
| `Ctrl+K` / `⌘K`         | Command palette (any page)                     |
| `Enter` / `Shift+Enter` | Send the prompt / new line                     |
| `Ctrl+S`                | Save the open file in the Code tab             |
| `Esc`                   | Close a menu, dialog or the projects drawer    |
| `/` and `N`             | Search projects / new project (Projects page)  |
| `Alt+↑` / `Alt+↓`       | Move an agent up or down (Agents page)         |

## The agent team

The 30 shared agents, in run order. "On" agents run by default; the others are helpers you switch on per project, pick
with a use-case team, or the Orchestrator brings in when a request needs them.

| Agent          | Default  | Model    | What it does |
|----------------|----------|----------|--------------|
| Brief          | required | haiku    | Turns your prompt into a brief: goal, audience, tone, success criteria |
| Research       | off      | sonnet   | Researches the idea on the web (competitors, reviews, trends) and writes a sourced report |
| Site auditor   | off      | sonnet   | Studies an existing or imported site and writes a prioritised list of what to fix |
| UX             | on       | opus     | Plans the structure: sections, navigation, user flow, calls to action |
| Design System  | on       | opus     | Palette, fonts, type scale, spacing and radii as CSS variables |
| 3D designer    | off      | opus     | Plans the site's 3D: which elements get depth, the object, interaction and fallbacks |
| Copywriter     | on       | sonnet   | Writes every word: headlines, body text, buttons, form labels and errors |
| Content writer | off      | sonnet   | Longer content: about and service pages, articles, FAQs and guides |
| HTML           | on       | sonnet   | Semantic, accessible markup from the plan and the copy |
| Translator     | off      | sonnet   | One copy of every page per language, a language switcher, right-to-left layouts |
| Legal pages    | off      | sonnet   | Privacy policy, legal notice and cookie information, linked from the footer |
| CSS            | on       | opus     | Styles everything with the tokens: layout, hierarchy, states, mobile first |
| Dark mode      | off      | sonnet   | A dark theme that follows the visitor's system, with a remembered toggle |
| Pricing        | off      | sonnet   | Plans to compare, service price lists or restaurant menus |
| Gallery        | off      | sonnet   | A fast, accessible photo gallery with a lightbox |
| Assets         | off      | sonnet   | Logo, favicon, icons and illustrations as hand-written SVG |
| JS             | on       | sonnet   | Vanilla JavaScript: menu, tabs, forms, scrolling |
| 3D scene       | off      | opus     | A real interactive 3D object in hand-written WebGL (no libraries), with fallbacks |
| Forms          | off      | sonnet   | Contact, booking, quote and newsletter forms with validation and clear states |
| Responsive     | on       | sonnet   | Works from 320px phones to wide desktops |
| Animation      | on       | sonnet   | Tasteful motion that respects reduced-motion settings |
| Depth effects  | off      | sonnet   | CSS 3D: tilt cards, layered parallax, flip cards, pressable buttons |
| Conversion     | off      | opus     | A clearer main action, trust signals, answers to objections, using only real facts |
| Accessibility  | on       | sonnet   | Audits against WCAG 2.2 AA and fixes what it finds |
| SEO            | off      | haiku    | Title, meta description, Open Graph tags, structured data |
| Local business | off      | haiku    | Hours, address, map link, click-to-call, WhatsApp and local search data |
| Performance    | off      | haiku    | Leaner CSS and JS, smart loading, no layout shift |
| Tidy           | off      | sonnet   | Cleans the code without changing how the site looks or works |
| QA             | off      | haiku    | Tests links, buttons, forms and selectors with a browser check, and fixes bugs |
| Critic         | on       | opus     | Reviews with a browser check, fixes small issues, sends big ones back, scores it |

**Research** is the only agent with web access (`tools: web`): it may only write `research.md` and the
`## Research` section of `plan.md`, and the server undoes any other change it makes. Its report opens in the studio's
**Research** tab.

### Teams

A **team** is a set of agents that run together, always in the agents' order. Pick one in a project's Team panel, or
manage them on the **Teams** page. Four built-in teams can be viewed and duplicated but not changed:

| Team    | Agents                             | Use it for                              |
|---------|------------------------------------|-----------------------------------------|
| Default | the agents marked "on"             | A new page                              |
| Quick   | HTML, CSS                          | Small changes, fastest and cheapest     |
| Polish  | Accessibility, Performance, Critic | Improving a page that already exists    |
| Full    | all of them                        | The most thorough result                |

And **22 shared use-case teams**, each with a starting prompt that fills the prompt box when you pick it:

| Build something new | Improve a site you have | Add one thing |
|---|---|---|
| New website from scratch · Landing page that converts · Restaurant or café · Local service business · Portfolio · Blog or content site · Product page · Event page · 3D website · Research, then build · Build from a sketch or screenshot | Improve an imported site · Redesign: new look, same content · Boost conversions · SEO and speed checkup · Accessibility audit · Code cleanup | Make it multilingual · Legal pages · Add a dark mode · Contact and booking forms · Add 3D to my site |

Users can save their own teams (with their own starting prompt) from the studio or the Teams page.

All shared agents and teams are seeded by database migrations on the first start (see below), so every user of a new
installation has them; a test checks that a fresh database gets everything in `agents/`.

## The marketplace

The app works like a freelance platform where the freelancers are agents (**Hire**, `/hire`):

- **Catalogue:** agents and teams by category (planning, writing, design, code, growth, quality; build, improve, add),
  with search, Official/Community filters, sorting (jobs done, hires, rating, newest) and pages. Each card shows the
  average cost and time per job (an estimate from its model until it has run), its rating and its jobs.
- **Profiles** (`/hire/agents/<n>`, `/hire/teams/<n>`): jobs done, projects, average time and cost, the Critic's average
  score (parsed from its "Score: x/10"), ratings with comments, a portfolio, the teams it belongs to and its full
  instructions. The portfolio shows your own projects, and other members' only if their owner ticked **Show this site
  in the portfolios of the agents that built it** on the studio's Team finished card.
- **Start a job:** pick one of your projects (or start a new one) and the agent joins its team, or the team becomes its
  team; the studio opens ready for your request.
- **Ratings:** when an agent finishes, its card in the studio has a thumbs up and down, and a comment (owner only, one
  rating per agent per project).
- **Community agents and teams:** on your own agent or team page, **Publish to the marketplace** with a category. An admin
  approves or rejects it (with a note) in the review queue at the top of `/hire`; editing a listed one sends it back for
  review. **Hire** copies it into your agents (a team brings its private agents along); hires are counted on the original.

## Customising the agents

Agents are stored in Postgres and edited on the **Agents** page. The shared agents and teams come from **database
migrations** in `server/migrations/`, which the server applies on startup, once each, in order:

| Migration | What it does |
|---|---|
| `0001_schema.sql` | the tables |
| `0002_shared_origin.sql` | remembers which shared agents and teams an admin edited or deleted |
| `0003_shared_agents_and_teams.json` | the first 27 shared agents and 20 use-case teams |
| `0004_marketplace.sql` | listings (publish, review), hires and ratings |
| `0005_3d_agents.json` | the three 3D agents and the two 3D teams |

To change the shared agents or teams, edit `agents/*.md` or `agents/_presets.json`, then run
`.venv/bin/python server/manage.py make-agents-migration`. It writes the next migration with what changed, applied on
the next start (or now with `manage.py migrate`; `manage.py migrations` lists them). A migration never overwrites an
agent or team an admin edited in the app, and never brings back one an admin deleted. Never edit a migration that has
been applied; add a new one. `agents/_common.md` (the shared rules) and `agents/_orchestrator.md` stay files and apply
on the next run.

The format, used by those files and by export and import, is a short header, then the instructions:

```markdown
---
id: faq
name: FAQ
icon: message
color: "#334455"
default: false
model: sonnet
description: Adds a FAQ section that answers the questions visitors really ask.
---
You are the FAQ agent. Add a FAQ section with 5 to 8 real questions and short answers,
using the existing styles. Don't change other sections.
```

- **Create, view and edit agents** on the Agents pages (`/agents`, `/agents/new`, `/agents/<id>`). Changes apply to
  the next run, with no restart needed.
- **Models come from a list, not free text:** the aliases your Claude CLI advertises (`fable`, `opus`, `sonnet`),
  the current full model names (`claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-fable-5-1`), and
  any model you add with **Another model… → Check with Claude CLI**.
- **Drag agents** on the Agents page to reorder them (an admin's order is the shared order).
- Full details are in [doc.md](doc.md#3-agents).

## Sharing agents and teams

| Export                          | Where                                   | File                                             |
|---------------------------------|-----------------------------------------|--------------------------------------------------|
| One agent                       | its page → **Export**                   | `<id>.md`: the agent file itself                 |
| One team, with its agents       | its page → **Export**                   | `team-<id>.json`                                 |
| Everything                      | Agents or Teams page → **Export all**   | `.zip` with all agents, shared rules and your teams |

**Import** (Agents or Teams page, or the command palette) accepts any of these files. You always see a preview first:
what will be added, replaced, kept, or can't be imported, and why. When something with the same id already exists,
choose **Keep both** (imported as a copy), **Replace mine** or **Keep mine**.

## Languages, theme and colours

- **Interface languages:** English, French and **Moroccan Darija** (Arabic script, right to left). Pick one on the
  login page, in the footer or on **/profile**; it's saved on the account. In French or Darija, the interface and the
  account emails are translated, and the agents write their chat messages in that language. The generated site keeps
  following the brief, so a French interface can build an English site.
- **How translation works:** pages are written in English; `server/static/i18n.js` translates them as they render,
  from `i18n-fr.js` and `i18n-ary.js` (keyed by the exact English text, with patterns for text containing numbers or
  names). What people wrote (project names, prompts, chat) is never translated. To add a language: add its code to
  `SUPPORTED` (and `RTL` if needed) and `LANGS` in `i18n.js`, write `i18n-<code>.js`, add it to `LANGUAGES` and
  `EMAILS` in `server/auth.py`, and to `language_rule` in `server/main.py`.
- **Darija** was translated by the AI. Have a native speaker review `server/static/i18n-ary.js` before launch.
- **Theme:** dark (default), light or follow the system, from **/profile** or the theme button; saved on the account.
- **Colours:** three colours on neutral backgrounds: **Majorelle blue** for actions, **saffron** for highlights and
  **zellige green** for success. Icon tiles, list cards and chart bars take turns between the three. The tokens are at
  the top of `server/static/base.css` (light and dark), and `server/static/theme.css` holds the component styles.
  Generated sites keep their own colours.

## Accounts, privacy and protection

- **Sign-up** asks for a name, email, password and acceptance of the terms; company, role, what you build sites for
  and how you heard about the app are optional (admins see the totals on **/analytics**). The email is confirmed with a
  6-digit code.
- **Brand kit, per project:** each project has its own logo, colours and fonts, set in the new-project form or in the
  studio (palette button), and copyable from another of your projects. A kit saved on the account by an earlier version
  is offered once as a copy source.
- **Profile** (`/profile`): language and theme, your details, password change (logs out other devices and sends a
  notice), log out other devices, **Download my data** (a .zip of your account, agents, teams, ratings and every
  project with its chat and files), and **Delete my account**.
- **Forgot your password?** on the login page emails a 6-digit reset code, with limits on sends and wrong tries.
- **GDPR basics:** **/terms** and **/privacy** pages (fill in `LEGAL_NAME`, `LEGAL_EMAIL` and `LEGAL_ADDRESS`), terms
  accepted at sign-up with the date stored, a one-cookie notice (no tracking or advertising cookies), data export and
  account deletion.
- **Sign-up protection:** per-IP limits on sign-ups, logins and reset requests, a hidden honeypot field, and throwaway
  email domains refused (`server/disposable_domains.txt`). Behind a reverse proxy, set `TRUST_PROXY=true`.
- When you serve it publicly, use HTTPS and set `COOKIE_SECURE=true`.

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
  - **Locked to the project folder.** Each agent runs Claude Code in restricted mode with only file tools. It has no
    shell, no MCP servers, and none of your personal Claude settings; only Research can use the web.
  - **Data is marked as data.** Project details, notes, browser checks and image paths are passed in labelled blocks
    and cleaned so they can't fake their way out. Security rules are added last and can't be edited away.
  - **You're told when something's off.** A blocked attempt, or a page that starts loading scripts, iframes or data
    from another site, shows a **Security check** card in the chat.
  - **Generated pages are sandboxed.** In the preview, in thumbnails or in their own tab, they run in an isolated
    origin, so their JavaScript can't use the app's API.
  - **Requests are guarded.** Unknown host names and cross-site writes are refused, and every page and API needs a
    confirmed login except the public pages.
- **Limits:** requests up to 8 MB; reference images up to 5 MB each (PNG, JPG, WebP, GIF), up to 6 per prompt; files
  up to 500 KB in the Code tab; one agent run up to `AGENT_TIMEOUT`.

## FAQ

**Do I need an API key?**
No. Agents Atelier runs Claude Code, which uses its existing login and plan.

**What does it produce?**
A static front end: one HTML file per page (`index.html` is the home page), a shared `style.css` and `script.js`,
plus SVG files in `assets/` if the Assets agent is on. No framework, no build step: it works by opening `index.html`
or on any static host.

**Can it build multi-page or multilingual sites?**
Yes. List the pages when you create the project. For several languages, use the **Make it multilingual** use case:
the Translator adds a copy of each page per language, a language switcher and right-to-left layouts.

**Can it build a back end, a database or logins?**
No, the generated sites are front ends only. A contact form or login screen will look and behave right, but nothing
is sent or stored; wire it to your own back end afterwards.

**Why is it slower than single-AI builders?**
Ten specialists each do a careful pass on the first build. After that, the Orchestrator runs only the agents a change
needs. To narrow it further, pick a smaller team or switch agents off, or start the server with `AGENT_MODEL=sonnet`.

**What if I don't like what one agent did?**
Click the retry button on its card and add an instruction, or open **Versions** and restore the code from before
that agent ran.

**Can I edit the code myself?**
Yes, in the Code tab or in any editor. The agents read the current files on the next prompt.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The suite (`tests/test_server.py`, about 150 tests in roughly 80 seconds) needs Postgres running
(`docker compose -f database/docker-compose.yaml up -d`, or set `TEST_DATABASE_URL`). It starts its own server on a free
port, with a throwaway database, a temporary copy of `agents/` and an empty `work/` folder, so your data is never
touched. It uses a **fake Claude CLI** (`tests/fake-claude`), so no model is called and nothing is spent. It covers:
- every page and API error path, and who may see what (guest, unconfirmed, user, admin)
- accounts: sign-up, codes, password reset and change, sessions, preferences, deletion, data export
- sign-up protection, per-project brand kits, analytics, legal details
- the marketplace: publishing, review, hiring, ratings, Critic scores, portfolios, and the first-run seed
- request guards (foreign Host, cross-site writes, body size)
- files, uploads, imports and versions
- agents, models, teams, use cases and the migrations
- WebSocket messages and full runs: stop / continue / start again / retry, timeouts, CLI errors
- the agents' language rule (French and Darija) and the emails in each language
- browser checks and the prompt-injection defences

Set `KEEP_TEST_FILES=1` to keep the temporary folder for inspection.

## Troubleshooting

| You see                                | What to do                                                                     |
|----------------------------------------|--------------------------------------------------------------------------------|
| "Claude Code wasn't found"             | Install Claude Code, or set `CLAUDE_BIN` to its path, then restart the server  |
| "Claude Code isn't logged in"          | Run `claude` in a terminal, log in, then press **Continue**                    |
| "rate-limited or overloaded"           | Wait a little, then press **Continue**                                         |
| No email arrives                       | Without `SMTP_HOST` the code is in the server log; otherwise check the `SMTP_*` settings |
| "Too many … from your network"         | Wait, raise the limit, or set `TRUST_PROXY=true` behind a reverse proxy        |
| No browser check card in the chat      | Install Chrome or Chromium, or set `CHROME_BIN`; check `BROWSER_CHECKS`        |
| "Can't reach the server"               | Start it with `./run.sh`, or check the port                                    |
| A banner saying the connection dropped | The studio reconnects by itself; agents keep working on the server            |
| An agent "ran for more than N minutes" | Press **Continue**, or raise `AGENT_TIMEOUT`                                   |
| A **Security check** card in the chat  | Read what was blocked or found, check the files, restore a version if needed   |
| "Unknown host" or "other websites"     | Open the app at `http://localhost:<port>` (set `ATELIER_HOSTS` to allow another name) |
| An error with a **ref** code           | Search the server's terminal output for that ref to see the full details       |

## Project layout

```text
.
├── README.md            this file
├── doc.md               technical documentation
├── run.sh               start the server
├── .env.example         every setting, documented (copy it to .env)
├── docs/screenshots/    screenshots used in this README
├── agents/              the shared agents' source files, shared rules, Orchestrator instructions, use-case teams
├── database/            docker-compose.yaml for Postgres
├── server/              the Python server (FastAPI)
│   ├── migrations/      numbered database migrations (schema, shared agents and teams)
│   ├── static/          the web pages, styles, icons and translations (i18n-fr.js, i18n-ary.js)
│   └── manage.py        make-admin, migrate, migrations, make-agents-migration
├── tests/               automated tests and a fake Claude CLI
└── work/                your projects, chat history and versions (created at first run, not in git)
```
