/* ---------- theme: light · dark · system (applied before the page paints) ---------- */
(function () {
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const pref = () => { try { return localStorage.getItem("theme") || "system"; } catch { return "system"; } };
  const apply = () => {
    const p = pref();
    document.documentElement.dataset.theme = p === "system" ? (media.matches ? "dark" : "light") : p;
    document.documentElement.dataset.themePref = p;
    document.querySelectorAll("[data-theme-btn]").forEach(b => { b.innerHTML = icon(themeIcon(), { size: 17 }); b.title = themeTitle(); });
  };
  const themeIcon = () => ({ light: "sun", dark: "moon", system: "monitor" })[pref()];
  const themeTitle = () => `Theme: ${pref()} — click to change`;
  window.getTheme = pref;
  window.setTheme = (p) => { try { localStorage.setItem("theme", p); } catch {} apply(); };
  window.cycleTheme = () => { const order = ["system", "light", "dark"]; setTheme(order[(order.indexOf(pref()) + 1) % 3]); toast(`Theme: ${pref()}`); };
  window.themeIcon = themeIcon; window.themeTitle = themeTitle;
  media.addEventListener?.("change", () => pref() === "system" && apply());
  window.addEventListener("storage", (e) => e.key === "theme" && apply());
  document.documentElement.dataset.theme = pref() === "system" ? (media.matches ? "dark" : "light") : pref();
  window.applyTheme = apply;
})();

/* Small shared helpers: api(), esc(), ago(), toast(), confirmDialog(). */
window.esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

class ApiError extends Error {
  constructor(message, status = 0) { super(message); this.name = "ApiError"; this.status = status; }
}
window.ApiError = ApiError;

/* fetch JSON from the server. Always throws an ApiError with a readable message:
   server down, timeout, HTML error page, validation errors… */
window.api = async (path, opts = {}) => {
  const { timeout = 30000, ...init } = opts;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  let res;
  try {
    res = await fetch(path, { headers: { "Content-Type": "application/json" }, signal: ctrl.signal, ...init });
  } catch (e) {
    throw new ApiError(e.name === "AbortError"
      ? "The server took too long to answer. Try again in a moment."
      : "Can't reach the server. Check that it's running (./run.sh), then try again.");
  } finally { clearTimeout(timer); }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : {}; } catch { data = null; }
  if (!res.ok) {
    let msg = data?.detail;
    if (Array.isArray(msg)) msg = msg.map(d => d.msg || JSON.stringify(d)).join("; ");
    else if (msg && typeof msg !== "string") msg = JSON.stringify(msg);
    if (!msg) msg = res.status === 413 ? "That's too large for the server to accept."
      : res.status >= 500 ? `The server had a problem (${res.status}). Check the server log.`
      : `Request failed (${res.status} ${res.statusText}).`;
    throw new ApiError(msg, res.status);
  }
  if (data === null) throw new ApiError("The server sent an unexpected answer.", res.status);
  return data;
};

window.fmtSize = (n = 0) => n < 1024 ? `${n} B` : n < 1024 ** 2 ? `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB` : `${(n / 1024 ** 2).toFixed(1)} MB`;

/* An inline "couldn't load" block with a Try again button. */
window.showError = (el, err, onRetry, title = "Couldn't load this") => {
  if (!el) return;
  el.innerHTML = `<div class="err-state" role="alert"><span class="tile">${icon("alert", { size: 22 })}</span>
    <b>${esc(title)}</b><p>${esc(err?.message || String(err))}</p>
    ${onRetry ? `<button class="btn btn-ghost btn-sm" type="button" data-retry-load>${icon("refresh", { size: 14 })}Try again</button>` : ""}</div>`;
  el.querySelector("[data-retry-load]")?.addEventListener("click", (e) => { e.stopPropagation(); onRetry(); });
};

/* Last line of defence: nothing fails silently. Throttled so one problem shows one toast. */
let lastGlobalError = 0;
function reportGlobal(err) {
  if (!err || err.name === "AbortError") return;
  console.error(err);
  const now = Date.now();
  if (now - lastGlobalError < 4000) return;
  lastGlobalError = now;
  if (document.body) toast(err.message || "Something went wrong on this page.", "err");
}
window.addEventListener("unhandledrejection", (e) => reportGlobal(e.reason));
window.addEventListener("error", (e) => reportGlobal(e.error));

window.ago = (t) => {
  const s = Date.now() / 1000 - t;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)} d ago`;
  return new Date(t * 1000).toLocaleDateString();
};

window.duration = (sec) => {
  sec = Math.max(0, Math.round(sec));
  return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
};

window.toast = (text, kind = "") => {
  let box = document.querySelector(".toasts");
  if (!box) { box = document.createElement("div"); box.className = "toasts"; document.body.append(box); }
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `${icon(kind === "err" ? "alert" : "check", { size: 16 })}<span>${esc(text)}</span>`;
  box.append(el);
  setTimeout(() => el.remove(), 2600);
};

/* Promise<boolean>. A styled replacement for window.confirm(). */
window.confirmDialog = ({ title, text, ok = "Confirm", danger = false }) => new Promise((resolve) => {
  const d = document.createElement("dialog");
  d.className = "modal";
  d.innerHTML = `<h3>${esc(title)}</h3><p>${esc(text)}</p>
    <div class="row"><button class="btn btn-ghost" value="no">Cancel</button>
    <button class="btn ${danger ? "btn-danger" : "btn-dark"}" value="yes">${esc(ok)}</button></div>`;
  document.body.append(d);
  d.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (b) d.close(b.value); else if (e.target === d) d.close("no");
  });
  d.addEventListener("close", () => { resolve(d.returnValue === "yes"); d.remove(); });
  d.showModal();
  d.querySelector('[value="yes"]').focus();
});

/* Name + description dialog. Both fields are required.
 * Resolves to { name, description } or null if cancelled. */
window.projectDialog = ({ title = "New project", ok = "Create project", name = "", description = "", pages = ["Home"], note = "", cancellable = true } = {}) =>
  new Promise((resolve) => {
    const d = document.createElement("dialog");
    d.className = "modal project-modal";
    d.innerHTML = `
      <form method="dialog" novalidate>
        <h3>${esc(title)}</h3>
        <p>${esc(note || "Every project has a name and a short description. The agents read both, so be specific.")}</p>
        <label class="pfield"><span>Name</span>
          <input class="input" name="name" maxlength="80" required placeholder="e.g. Bean There coffee shop" value="${esc(name)}">
          <small class="err" data-for="name"></small></label>
        <label class="pfield"><span>Description <em data-count>0/300</em></span>
          <textarea class="input" name="description" maxlength="300" rows="3" required
            placeholder="e.g. A cozy neighbourhood café in Lyon. One page with the menu, opening hours and a booking button.">${esc(description)}</textarea>
          <small class="err" data-for="description"></small></label>
        <label class="pfield"><span>Pages <em>one per line · the first is the home page</em></span>
          <textarea class="input" name="pages" rows="3" placeholder="Home&#10;About&#10;Contact">${esc((pages || ["Home"]).join("\n"))}</textarea>
          <small class="err" data-for="pages"></small></label>
        <div class="row">
          ${cancellable ? `<button class="btn btn-ghost" value="cancel" formnovalidate>Cancel</button>` : ""}
          <button class="btn btn-dark" value="ok">${esc(ok)}</button>
        </div>
      </form>`;
    document.body.append(d);
    const form = d.querySelector("form"), f = form.elements;
    const count = () => d.querySelector("[data-count]").textContent = `${f.description.value.length}/300`;
    count(); f.description.addEventListener("input", count);
    const check = () => {
      let good = true;
      for (const key of ["name", "description"]) {
        const empty = !f[key].value.trim();
        f[key].setAttribute("aria-invalid", empty);
        d.querySelector(`[data-for="${key}"]`).textContent = empty ? `Add a ${key} for this project.` : "";
        if (empty && good) { f[key].focus(); good = false; }
      }
      return good;
    };
    let result = null;
    form.addEventListener("submit", (e) => {
      if (e.submitter?.value !== "ok") return;                 // cancel
      if (!check()) { e.preventDefault(); return; }
      const pageList = f.pages.value.split("\n").map(x => x.trim()).filter(Boolean);
      if (pageList.length > 12) { e.preventDefault(); d.querySelector('[data-for="pages"]').textContent = "Up to 12 pages."; return; }
      result = { name: f.name.value.trim(), description: f.description.value.trim(), pages: pageList.length ? pageList : ["Home"] };
    });
    f.description.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); form.requestSubmit(form.querySelector('[value="ok"]')); }
    });
    if (!cancellable) d.addEventListener("cancel", (e) => e.preventDefault());
    d.addEventListener("click", (e) => { if (cancellable && e.target === d) d.close(); });
    d.addEventListener("close", () => { resolve(result); d.remove(); });
    d.showModal();
    (f.name.value ? f.description : f.name).focus();
  });

/* Ask for name + description, create the project, open it. */
window.newProject = async () => {
  const details = await projectDialog();
  if (!details) return;
  try {
    const p = await api("/api/projects", { method: "POST", body: JSON.stringify(details) });
    location.href = `/p/${p.id}`;
  } catch (err) { toast(err.message, "err"); }
};

/* Ask for a short text. Resolves to the string (may be empty if `optional`) or null when cancelled. */
window.promptDialog = ({ title, text = "", label = "", placeholder = "", value = "", ok = "OK", optional = false, multiline = false, max = 300 }) =>
  new Promise((resolve) => {
    const d = document.createElement("dialog");
    d.className = "modal project-modal";
    const field = multiline
      ? `<textarea class="input" name="v" rows="3" maxlength="${max}" placeholder="${esc(placeholder)}">${esc(value)}</textarea>`
      : `<input class="input" name="v" maxlength="${max}" placeholder="${esc(placeholder)}" value="${esc(value)}">`;
    d.innerHTML = `<form method="dialog" novalidate>
      <h3>${esc(title)}</h3>${text ? `<p>${esc(text)}</p>` : ""}
      <label class="pfield"><span>${esc(label)}${optional ? " <em>optional</em>" : ""}</span>${field}<small class="err"></small></label>
      <div class="row"><button class="btn btn-ghost" value="cancel" formnovalidate>Cancel</button><button class="btn btn-dark" value="ok">${esc(ok)}</button></div>
    </form>`;
    document.body.append(d);
    const form = d.querySelector("form"), input = form.elements.v;
    let result = null;
    form.addEventListener("submit", (e) => {
      if (e.submitter?.value !== "ok") return;
      const v = input.value.trim();
      if (!v && !optional) { e.preventDefault(); d.querySelector(".err").textContent = "This can't be empty."; input.focus(); return; }
      result = v;
    });
    input.addEventListener("keydown", (e) => {
      if (multiline && e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); form.requestSubmit(form.querySelector('[value="ok"]')); }
    });
    d.addEventListener("click", (e) => { if (e.target === d) d.close(); });
    d.addEventListener("close", () => { resolve(result); d.remove(); });
    d.showModal(); input.focus();
  });

window.money = (usd) => usd >= 1 ? `$${usd.toFixed(2)}` : usd >= 0.01 ? `$${usd.toFixed(3)}` : usd > 0 ? `$${usd.toFixed(4)}` : "$0";
window.compact = (n) => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n || 0);


/* ---------- import agents / teams (used by the Agents and Teams pages and the command palette) ---------- */
window.importFlow = () => new Promise((resolve) => {
  const input = document.createElement("input");
  input.type = "file"; input.accept = ".md,.json,.zip";
  input.onchange = async () => {
    const file = input.files[0]; if (!file) return resolve(false);
    if (file.size > 6 * 1024 * 1024) { toast("That file is larger than 6 MB", "err"); return resolve(false); }
    const data = await new Promise((r) => { const fr = new FileReader(); fr.onload = () => r(String(fr.result).split(",")[1] || ""); fr.readAsDataURL(file); });
    let policy = "rename";
    const plan = async () => api("/api/import", { method: "POST", body: JSON.stringify({ name: file.name, data, on_conflict: policy }) });
    let p;
    try { p = await plan(); } catch (err) { toast(err.message, "err"); return resolve(false); }
    const d = document.createElement("dialog");
    d.className = "modal project-modal import-modal";
    const LABEL = { add: "Add", rename: "Add as copy", replace: "Replace", skip: "Skip", same: "Already have", error: "Can't import" };
    const render = () => {
      const doable = p.items.filter(i => ["add", "rename", "replace"].includes(i.action)).length;
      d.innerHTML = `<h3>Import ${esc(file.name)}</h3>
        <p>Check what will happen before anything changes.</p>
        <label class="pfield"><span>When something already exists</span>
          <select class="input" id="impPolicy">
            <option value="rename" ${policy === "rename" ? "selected" : ""}>Keep both — import as a copy</option>
            <option value="replace" ${policy === "replace" ? "selected" : ""}>Replace mine with the imported one</option>
            <option value="skip" ${policy === "skip" ? "selected" : ""}>Keep mine — skip it</option></select></label>
        <ul class="imp-list">${p.items.map(i => `<li class="imp-${i.action}">
          <span class="imp-kind">${icon(i.kind === "team" ? "layers" : i.kind === "rules" ? "file" : "bot", { size: 14 })}</span>
          <span class="imp-main"><b>${esc(i.name || i.id || "")}</b>${i.target && i.target !== i.id ? ` <span class="mono">→ ${esc(i.target)}</span>` : ""}
          ${i.note ? `<small>${esc(i.note)}</small>` : ""}</span><span class="badge">${LABEL[i.action] || i.action}</span></li>`).join("")}</ul>
        <div class="row"><button class="btn btn-ghost" value="cancel">Cancel</button>
          <button class="btn btn-dark" value="ok" ${doable ? "" : "disabled"}>${doable ? `Import ${doable} item${doable === 1 ? "" : "s"}` : "Nothing to import"}</button></div>`;
      d.querySelector("#impPolicy").onchange = async (e) => {
        policy = e.target.value;
        try { p = await plan(); render(); } catch (err) { toast(err.message, "err"); }
      };
    };
    render();
    document.body.append(d);
    d.addEventListener("click", async (e) => {
      const b = e.target.closest("button[value]");
      if (e.target === d || b?.value === "cancel") { d.close(); return resolve(false); }
      if (b?.value === "ok") {
        b.disabled = true;
        try {
          const r = await api("/api/import", { method: "POST", body: JSON.stringify({ name: file.name, data, on_conflict: policy, apply: true }) });
          toast(`Imported ${r.applied} item${r.applied === 1 ? "" : "s"}`); d.close(); resolve(true);
        } catch (err) { toast(err.message, "err"); b.disabled = false; }
      }
    });
    d.addEventListener("close", () => d.remove());
    d.showModal();
  };
  input.click();
});

/* ---------- command palette: Ctrl/Cmd+K on every page ---------- */
(function () {
  const providers = [];
  /* Pages add their own commands: a list of {title, group, icon, run, hint?, when?},
     or a function returning such a list (called each time the palette opens). */
  window.registerCommands = (listOrFn) => providers.push(listOrFn);
  const pageCommands = () => providers.flatMap(p => typeof p === "function" ? p() : p).filter(c => !c.when || c.when());
  let cache = null, cacheAt = 0;

  async function dynamicCommands() {
    if (cache && Date.now() - cacheAt < 20000) return cache;
    const [projects, agents, teams] = await Promise.all([
      api("/api/projects").catch(() => []), api("/api/agents").catch(() => []), api("/api/presets").catch(() => [])]);
    cache = [
      ...projects.map(p => ({ group: "Projects", icon: p.busy ? "zap" : "folder", title: p.name, hint: p.description, run: () => location.href = `/p/${p.id}` })),
      ...agents.map(a => ({ group: "Agents", icon: a.icon, title: a.name, hint: a.description, run: () => location.href = `/agents/${encodeURIComponent(a.id)}` })),
      ...teams.map(t => ({ group: "Teams", icon: t.builtin ? "users" : "sliders", title: t.name, hint: `${t.agents.length} agents${t.description ? " · " + t.description : ""}`,
                          run: () => location.href = `/teams/${encodeURIComponent(t.id)}` })),
    ];
    cacheAt = Date.now();
    return cache;
  }
  const staticCommands = () => [
    { group: "Go to", icon: "home", title: "Home", run: () => location.href = "/" },
    { group: "Go to", icon: "grid", title: "Projects", run: () => location.href = "/projects" },
    { group: "Go to", icon: "users", title: "Agents", run: () => location.href = "/agents" },
    { group: "Go to", icon: "layers", title: "Teams", run: () => location.href = "/teams" },
    { group: "Go to", icon: "book", title: "Guide", run: () => location.href = "/guide" },
    { group: "Go to", icon: "file", title: "Shared rules", run: () => location.href = "/agents/rules" },
    { group: "Create", icon: "plus", title: "New project", run: () => newProject() },
    { group: "Create", icon: "plus", title: "New agent", run: () => location.href = "/agents/new" },
    { group: "Create", icon: "plus", title: "New team", run: () => location.href = "/teams/new" },
    { group: "Agents & teams", icon: "download", title: "Export all agents and teams (.zip)", run: () => location.href = "/api/export" },
    { group: "Agents & teams", icon: "file-plus", title: "Import agents or teams…", run: async () => { if (await importFlow()) location.reload(); } },
    { group: "Appearance", icon: "sun", title: "Theme: light", run: () => setTheme("light") },
    { group: "Appearance", icon: "moon", title: "Theme: dark", run: () => setTheme("dark") },
    { group: "Appearance", icon: "monitor", title: "Theme: follow the system", run: () => setTheme("system") },
  ];

  /* How well `q` matches `text`: 0 = no match. Substrings and word starts win; scattered letters only count
     when they stay close together, so "css" doesn't match "follow the system". */
  function score(q, text) {
    if (!q) return 1;
    const t = (text || "").toLowerCase();
    const at = t.indexOf(q);
    if (at === 0) return 100 - t.length / 100;
    if (at > 0) return (" -/·:".includes(t[at - 1]) ? 80 : 60) - t.length / 100;
    let i = 0, s = 0, prev = -1, gaps = 0;
    for (const ch of q) {
      const j = t.indexOf(ch, i);
      if (j < 0) return 0;
      if (prev >= 0) gaps += j - prev - 1;
      s += j === prev + 1 ? 3 : (j === 0 || " -/·:".includes(t[j - 1]) ? 2 : 0);
      prev = j; i = j + 1;
    }
    return gaps > q.length * 2 ? 0 : 20 + s - gaps;
  }

  let dlg, list = [], active = 0;
  async function open() {
    if (dlg?.open) return dlg.querySelector("input").select();
    dlg = document.createElement("dialog");
    dlg.className = "palette";
    dlg.setAttribute("aria-label", "Command palette");
    dlg.innerHTML = `<div class="pal-input">${icon("search", { size: 18 })}<input type="text" placeholder="Search projects, agents, teams and actions…"
        aria-label="Search" autocomplete="off" spellcheck="false"><kbd>Esc</kbd></div>
      <div class="pal-list" role="listbox"></div>
      <div class="pal-foot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>Ctrl</kbd>+<kbd>K</kbd> anywhere</span></div>`;
    document.body.append(dlg);
    const input = dlg.querySelector("input"), box = dlg.querySelector(".pal-list");
    let all = [...pageCommands(), ...staticCommands()];
    const render = () => {
      const q = input.value.trim().toLowerCase();
      list = all.map(c => ({ c, s: Math.max(score(q, c.title), c.group.toLowerCase().startsWith(q) ? 50 : 0,
                                             c.hint && c.hint.toLowerCase().includes(q) ? 30 : 0) }))
                .filter(x => x.s > 0).sort((a, b) => b.s - a.s).slice(0, 60).map(x => x.c);
      if (!q) list.sort((a, b) => all.indexOf(a) - all.indexOf(b));
      const groups = [...new Set(list.map(c => c.group))];          // each heading once, best group first
      list = groups.flatMap(g => list.filter(c => c.group === g));
      active = Math.min(active, Math.max(list.length - 1, 0));
      let group = "";
      box.innerHTML = list.map((c, i) => {
        const head = c.group !== group ? `<div class="pal-group">${esc(group = c.group)}</div>` : "";
        return `${head}<button class="pal-item ${i === active ? "on" : ""}" data-i="${i}" role="option" aria-selected="${i === active}">
          ${icon(c.icon || "arrow-right", { size: 16 })}<span><b>${esc(c.title)}</b>${c.hint ? `<small>${esc(c.hint)}</small>` : ""}</span></button>`;
      }).join("") || `<p class="pal-empty">Nothing matches “${esc(input.value)}”.</p>`;
      box.querySelector(".pal-item.on")?.scrollIntoView({ block: "nearest" });
    };
    const run = (i) => { const c = list[i]; if (!c) return; dlg.close(); try { c.run(); } catch (e) { toast(e.message, "err"); } };
    input.addEventListener("input", () => { active = 0; render(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, list.length - 1); render(); }
      if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); render(); }
      if (e.key === "Enter") { e.preventDefault(); run(active); }
    });
    box.addEventListener("mousemove", (e) => { const b = e.target.closest(".pal-item"); if (b && +b.dataset.i !== active) { active = +b.dataset.i; render(); } });
    box.addEventListener("click", (e) => { const b = e.target.closest(".pal-item"); if (b) run(+b.dataset.i); });
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
    dlg.addEventListener("close", () => { dlg.remove(); dlg = null; });
    active = 0; render(); dlg.showModal(); input.focus();
    try { all = [...all, ...(await dynamicCommands())]; if (dlg) render(); } catch {}
  }
  window.openPalette = open;
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); open(); }
  });

  /* header buttons: search (palette) and theme, added to every page */
  document.addEventListener("DOMContentLoaded", () => {
    const mac = /Mac|iPhone|iPad/.test(navigator.platform);
    const make = () => {
      const wrap = document.createElement("span");
      wrap.className = "hdr-tools";
      wrap.innerHTML = `<button class="icon-btn pal-btn" type="button" title="Search and commands (${mac ? "⌘" : "Ctrl+"}K)" aria-label="Search and commands">${icon("search", { size: 17 })}</button>
        <button class="icon-btn" type="button" data-theme-btn aria-label="Change theme">${icon(themeIcon(), { size: 17 })}</button>`;
      wrap.querySelector(".pal-btn").onclick = open;
      wrap.querySelector("[data-theme-btn]").onclick = () => cycleTheme();
      wrap.querySelector("[data-theme-btn]").title = themeTitle();
      return wrap;
    };
    const site = document.querySelector("header.top .wrap");
    if (site) site.insertBefore(make(), site.lastElementChild);
    const studio = document.querySelector(".chat .bar");
    if (studio) studio.insertBefore(make(), document.getElementById("menu"));
  });
})();

/* ---------- motion: stagger list items in as they appear, count numbers up ----------
   Anything matching ENTER gets .enter with --i = its position among its siblings, the first time it is added
   to the page. Studio tiles and chat messages re-render constantly, so they only animate during the first paint. */
(() => {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) { window.countUp = (el, v) => { el.textContent = v; }; return; }
  const ENTER = ".pcard, .grid > .row, .stat, .arow, .tcard, .card, .feature, .part, .stepc, .tip, .tm, .vrow, .kpi, .proj, .newcard, .err-state, .empty";
  const FIRST_PAINT_ONLY = ".agent-tile";
  const LANDS = ".pcard, .tcard, .feature, .tm, .proj, .newcard";
  let settled = false;
  const seen = new WeakSet();
  function mark(el) {
    if (seen.has(el) || el.closest(".reveal:not(.in)") || el.classList.contains("reveal")) return;
    seen.add(el);
    const sibs = [...el.parentElement.children].filter(c => c.matches(ENTER) || c.matches(FIRST_PAINT_ONLY));
    el.style.setProperty("--i", Math.max(0, sibs.indexOf(el)));
    el.classList.add("enter");
    if (el.matches(LANDS)) el.classList.add("lands");
    el.addEventListener("animationend", () => el.classList.remove("enter", "lands"), { once: true });
  }
  function scan(root) {
    if (!(root instanceof Element)) return;
    const sel = settled ? ENTER : ENTER + ", " + FIRST_PAINT_ONLY;
    if (root.matches(sel)) mark(root);
    root.querySelectorAll(sel).forEach(mark);
  }
  const mo = new MutationObserver(list => list.forEach(m => m.addedNodes.forEach(scan)));
  document.addEventListener("DOMContentLoaded", () => {
    scan(document.body);
    mo.observe(document.body, { childList: true, subtree: true });
    setTimeout(() => { settled = true; }, 1500);
  });

  /* count a number up from what is shown now to `value` (plain integers only) */
  window.countUp = (el, value) => {
    const to = Number(value), from = parseInt(el.textContent, 10) || 0;
    if (!Number.isFinite(to) || to === from) { el.textContent = value; return; }
    const t0 = performance.now(), dur = Math.min(900, 250 + Math.abs(to - from) * 40);
    const step = (t) => {
      const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      el.textContent = Math.round(from + (to - from) * e);
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };
})();

/* ---------- pagination, shared by every list ----------
   pageList(3, 10) -> [1, 2, 3, 4, "…", 10]
   paginate(items, page, perPage) -> { items, page, pages, total, perPage }   (page is clamped)
   pagerHTML(state, { sizes, noun }) -> the Prev / 1 2 3 / Next / Per page bar (empty when it fits on one page)
   bindPager(el, onPage, onPerPage) wires the bar's clicks.
   perPagePref(key, fallback) / savePerPage(key, n) remember the per-page choice per list. */
function pageList(cur, total) {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const set = new Set([1, total, cur, cur - 1, cur + 1]);
  if (cur <= 3) [2, 3, 4].forEach(n => set.add(n));
  if (cur >= total - 2) [total - 1, total - 2, total - 3].forEach(n => set.add(n));
  const nums = [...set].filter(n => n >= 1 && n <= total).sort((a, b) => a - b);
  const out = [];
  nums.forEach((n, i) => { if (i && n - nums[i - 1] > 1) out.push("…"); out.push(n); });
  return out;
}
function paginate(items, page, perPage) {
  const total = items.length, pages = Math.max(1, Math.ceil(total / perPage));
  page = Math.min(Math.max(1, page || 1), pages);
  return { items: items.slice((page - 1) * perPage, page * perPage), page, pages, total, perPage };
}
function pagerHTML({ page, pages, total, perPage }, { sizes = [10, 20, 50], noun = "", always = false } = {}) {
  if (!total || (!always && total <= Math.min(...sizes))) return "";
  const from = total ? (page - 1) * perPage + 1 : 0, to = Math.min(total, page * perPage);
  return `
    <span class="info">Showing <b>${from}–${to}</b> of ${total}${noun ? " " + noun : ""}</span>
    ${pages > 1 ? `<div class="pages">
      <button type="button" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""} aria-label="Previous page">${icon("arrow-right", { size: 16, cls: "flip" })}Prev</button>
      ${pageList(page, pages).map(n => n === "…" ? `<span class="gap">…</span>`
        : `<button type="button" class="num ${n === page ? "on" : ""}" data-page="${n}" ${n === page ? 'aria-current="page"' : ""} aria-label="Page ${n}">${n}</button>`).join("")}
      <button type="button" data-page="${page + 1}" ${page >= pages ? "disabled" : ""} aria-label="Next page">Next${icon("arrow-right", { size: 16 })}</button>
    </div>` : "<span></span>"}
    <label class="per">Per page
      <select class="input" data-per-page>${sizes.map(n => `<option ${n === perPage ? "selected" : ""}>${n}</option>`).join("")}</select></label>`;
}
function bindPager(el, onPage, onPerPage) {
  el.addEventListener("click", (e) => { const b = e.target.closest("[data-page]"); if (b && !b.disabled) onPage(+b.dataset.page); });
  el.addEventListener("change", (e) => { if (e.target.matches("[data-per-page]")) onPerPage(+e.target.value); });
}
function perPagePref(key, fallback) {
  try { return +localStorage.getItem("perPage:" + key) || fallback; } catch { return fallback; }
}
function savePerPage(key, n) { try { localStorage.setItem("perPage:" + key, n); } catch {} }

/* ---------- lazy loading helpers ----------
   lazyEach(root): <img data-src> / <iframe data-src> load when they come near the screen.
   onVisible(el, fn): run fn once, when el scrolls into view (used for "load more" sentinels). */
const lazyObserver = "IntersectionObserver" in window ? new IntersectionObserver((entries) => entries.forEach(e => {
  if (!e.isIntersecting) return;
  const el = e.target; lazyObserver.unobserve(el);
  if (el.dataset.src) { el.src = el.dataset.src; el.removeAttribute("data-src"); }
}), { rootMargin: "300px" }) : null;
function lazyEach(root = document) {
  root.querySelectorAll("img[data-src], iframe[data-src]").forEach(el => lazyObserver ? lazyObserver.observe(el) : (el.src = el.dataset.src));
}
function onVisible(el, fn, root = null) {
  if (!("IntersectionObserver" in window)) { fn(); return; }
  const io = new IntersectionObserver((entries) => { if (entries.some(e => e.isIntersecting)) { io.disconnect(); fn(); } }, { root, rootMargin: "200px" });
  io.observe(el);
  return io;
}
