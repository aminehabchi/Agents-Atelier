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
window.projectDialog = ({ title = "New project", ok = "Create project", name = "", description = "", note = "", cancellable = true } = {}) =>
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
      result = { name: f.name.value.trim(), description: f.description.value.trim() };
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
