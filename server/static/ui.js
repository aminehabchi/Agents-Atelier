/* ---------- theme: light · dark · system (applied before the page paints) ---------- */
(function () {
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const pref = () => { try { return localStorage.getItem("theme") || "dark"; } catch { return "dark"; } };
  const apply = () => {
    const p = pref();
    document.documentElement.dataset.theme = p === "system" ? (media.matches ? "dark" : "light") : p;
    document.documentElement.dataset.themePref = p;
    document.querySelectorAll("[data-theme-btn]").forEach(b => { b.innerHTML = icon(themeIcon(), { size: 17 }); b.title = themeTitle(); });
  };
  const themeIcon = () => ({ light: "sun", dark: "moon", system: "monitor" })[pref()];
  const themeTitle = () => `Theme: ${pref()}. Click to change`;
  window.getTheme = pref;
  window.setTheme = (p, save = true) => { try { localStorage.setItem("theme", p); } catch {} apply(); if (save) window.savePreferences?.({ theme: p }); };
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
  constructor(message, status = 0, upgrade = "") { super(message); this.name = "ApiError"; this.status = status; this.upgrade = upgrade; }
}
window.ApiError = ApiError;

/* The logged-in user ({ id, email, name, role, confirmed, … }), or null. Asked once per page. */
let mePromise = null;
/* <html data-auth="guest|unconfirmed|member">: elements with data-show="…" appear only for those visitors (base.css).
   The last known state is remembered so a logged-in page doesn't flash the guest header. It only changes what is
   shown: the server checks every request. */
const authStateOf = (user) => !user ? "guest" : user.confirmed ? "member" : "unconfirmed";
const authState = () => document.documentElement.dataset.auth;
try { document.documentElement.dataset.auth = localStorage.getItem("atelier-auth") || "guest"; }
catch { document.documentElement.dataset.auth = "guest"; }
function rememberAuth(user) {
  document.documentElement.dataset.auth = authStateOf(user);
  try { localStorage.setItem("atelier-auth", authStateOf(user)); } catch {}
  if (user?.confirmed) {       // the account's choices follow you to any device
    if (user.theme && user.theme !== getTheme()) setTheme(user.theme, false);
    if (user.language && user.language !== getLang()) setLang(user.language);
    else if (!user.language) savePreferences({ language: getLang() });
  }
  return user;
}
/* Saves language or theme on the account. Without a confirmed account they only stay in this browser. */
window.savePreferences = (prefs) => {
  if (document.documentElement.dataset.auth !== "member") return Promise.resolve();
  return fetch("/api/auth/preferences", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(prefs) }).catch(() => {});
};
window.getMe = () => mePromise ||= fetch("/api/auth/me").then(r => r.ok ? r.json() : { user: null }).then(d => rememberAuth(d.user)).catch(() => null);
const initialsOf = (user) => (user.name || user.email).trim().split(/\s+/).slice(0, 2).map(w => w[0]).join("").toUpperCase();
const PUBLIC_PAGES = ["/", "/login", "/register", "/forgot", "/confirm", "/terms", "/privacy"];

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
    // Private pages go to the right account page; public pages and the account forms show the message instead.
    if (!PUBLIC_PAGES.includes(location.pathname) && !path.startsWith("/api/auth/")) {
      if (res.status === 401) location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
      if (res.status === 403 && data?.detail === "Confirm your email first.") location.href = "/confirm";
    }
    let msg = data?.detail;
    if (Array.isArray(msg)) msg = msg.map(d => d.msg || JSON.stringify(d)).join("; ");
    else if (msg && typeof msg !== "string") msg = JSON.stringify(msg);
    if (!msg) msg = res.status === 413 ? "That's too large for the server to accept."
      : res.status >= 500 ? `The server had a problem (${res.status}). Check the server log.`
      : `Request failed (${res.status} ${res.statusText}).`;
    if (res.status === 403 && data?.upgrade) planPrompt(msg, data.upgrade);    // a feature of a higher plan
    throw new ApiError(msg, res.status, data?.upgrade || "");
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
  return new Date(t * 1000).toLocaleDateString(getLocale());
};

window.duration = (sec) => {
  sec = Math.max(0, Math.round(sec));
  return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
};

/* "Available on Pro": when the server refuses something the member's plan doesn't include, offer the plans
   instead of a bare error. The page's own error toast for the same message is skipped. */
const PLAN_NAMES = { pro: "Pro", agency: "Agency" };
let planPromptShown = { text: "", at: 0 };
window.planPrompt = (text, needs) => {
  planPromptShown = { text, at: Date.now() };
  if (document.querySelector("dialog.plan-prompt")) return;
  const name = PLAN_NAMES[needs] || "a higher plan";
  const d = document.createElement("dialog");
  d.className = "modal plan-prompt";
  d.innerHTML = `<span class="pp-ic">${icon("sparkles", { size: 22 })}</span>
    <h3>${needs === "agency" ? "Available on Agency" : "Available on Pro"}</h3><p>${esc(text)}</p>
    <div class="row"><button class="btn btn-ghost" value="no">Not now</button>
      <a class="btn btn-dark" href="/profile?upgrade=${encodeURIComponent(needs)}#plan">${icon("arrow-right", { size: 15 })}See the ${esc(name)} plan</a></div>`;
  document.body.append(d);
  d.addEventListener("click", (e) => { if (e.target === d || e.target.closest("[value=no]")) d.close(); });
  d.addEventListener("close", () => d.remove());
  d.showModal();
};

window.toast = (text, kind = "") => {
  if (kind === "err" && text === planPromptShown.text && Date.now() - planPromptShown.at < 4000) return;
  let box = document.querySelector(".toasts");
  if (!box) { box = document.createElement("div"); box.className = "toasts"; document.body.append(box); }
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `${icon(kind === "err" ? "alert" : "check", { size: 16 })}<span>${esc(text)}</span>`;
  box.append(el);
  setTimeout(() => el.remove(), 2600);
};

/* The account button in the header: your initials and first name, opening a menu with your pages and Log out.
   Esc, a click elsewhere or leaving the menu closes it. */
window.accountButton = (user, { compact = false } = {}) => {
  const ROLE = { admin: "Admin", user: "User", unconfirmed: "Not confirmed" };
  const box = document.createElement("div");
  box.className = "acct";
  const item = (href, ic, text) => `<a role="menuitem" href="${href}">${icon(ic, { size: 15 })}<span>${text}</span></a>`;
  box.innerHTML = `
    <button class="acct-btn ${compact ? "compact" : ""}" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="Account: ${esc(user.email)}" title="${esc(user.name || user.email)}">
      <span class="acct-initials">${esc(initialsOf(user))}</span>
      ${compact ? "" : `<span class="acct-name" data-no-i18n>${esc((user.name || user.email).split(/[\s@]/)[0])}</span>`}
      ${compact ? "" : icon("chevron-down", { size: 14 })}
    </button>
    <div class="acct-menu" role="menu" hidden>
      <div class="acct-head"><b data-no-i18n>${esc(user.name || user.email)}</b>${user.name ? `<small data-no-i18n>${esc(user.email)}</small>` : ""}<span class="badge">${esc(ROLE[user.role] || user.role)}</span>${user.role === "user" && user.plan ? ` <span class="badge plan-badge">${esc({ starter: "Starter", pro: "Pro", agency: "Agency" }[user.plan] || user.plan)}</span>` : ""}</div>
      <div class="acct-links">
        ${user.confirmed ? item("/profile", "user", "Profile") + item("/profile#plan", "sparkles", user.role === "user" && user.plan !== "agency" ? "Plan and upgrade" : "Plan") + item("/guide", "book", "Guide") : item("/confirm", "check", "Confirm your email")}
        ${user.role === "admin" ? item("/users", "users", "Users") : ""}
      </div>
      <div class="acct-links"><button type="button" role="menuitem" data-out>${icon("log-out", { size: 15 })}<span>Log out</span></button></div>
    </div>`;
  const btn = box.querySelector(".acct-btn"), menu = box.querySelector(".acct-menu");
  const close = (focus) => { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); if (focus) btn.focus(); };
  const open = () => { menu.hidden = false; btn.setAttribute("aria-expanded", "true"); menu.querySelector("a, button")?.focus(); };
  btn.onclick = () => (menu.hidden ? open() : close(true));
  document.addEventListener("click", (e) => { if (!box.contains(e.target)) close(); });
  box.addEventListener("focusout", (e) => { if (e.relatedTarget && !box.contains(e.relatedTarget)) close(); });
  box.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !menu.hidden) { e.stopPropagation(); close(true); }
    if (!["ArrowDown", "ArrowUp"].includes(e.key) || menu.hidden) return;
    e.preventDefault();
    const all = [...menu.querySelectorAll("a, .acct-links button")], i = all.indexOf(document.activeElement);
    all[(i + (e.key === "ArrowDown" ? 1 : -1) + all.length) % all.length]?.focus();
  });
  menu.addEventListener("click", async (e) => {
    if (e.target.closest("[data-out]")) {
      try { await api("/api/auth/logout", { method: "POST" }); location.href = "/"; }
      catch (err) { toast(err.message, "err"); }
    }
  });
  return box;
};
window.updateAccountButton = (user) => {
  const box = document.querySelector(".acct");
  if (!box) return;
  box.querySelector(".acct-initials").textContent = initialsOf(user);
  const name = box.querySelector(".acct-name");
  if (name) name.textContent = (user.name || user.email).split(/[\s@]/)[0];
  box.querySelector(".acct-head b").textContent = user.name || user.email;
  box.querySelector(".acct-btn").title = user.name || user.email;
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
 * Resolves to { name, description, pages } or null if cancelled.
 * With `allowImport`, the user can also start from an existing site: the result then has `source`
 * ({ zip: File } or { files: [{ path, file }] }) instead of pages. */
window.SITE_EXT = ["html", "htm", "css", "js", "mjs", "md", "png", "jpg", "jpeg", "gif", "webp", "avif", "svg", "ico",
  "woff", "woff2", "ttf", "otf", "mp4", "webm", "mp3", "ogg", "wav"];
const MAX_SITE_MB = 40;
const MAX_ASSET_MB = 5, MAX_ASSETS = 12;
const ASSET_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];
/* The questions a project asks (all optional). Keys match BRIEF_FIELDS in server/main.py. */
const BRIEF_QUESTIONS = [
  { key: "site_type", label: "What kind of site is it?", type: "select",
    options: ["Landing page", "Business / brochure site", "Portfolio", "Product page", "Restaurant or café", "Event", "Personal site or blog", "Other"] },
  { key: "audience", label: "Who is it for?", max: 200, placeholder: "e.g. Young families looking for weekend activities" },
  { key: "goal", label: "What should visitors do?", max: 200, placeholder: "e.g. Book a table, call us, join the newsletter" },
  { key: "tone", label: "Tone and style", type: "select",
    options: ["Friendly and warm", "Professional and trustworthy", "Minimal and calm", "Bold and energetic", "Luxurious and elegant", "Playful and fun", "Technical and precise"] },
  { key: "language", label: "Language(s) of the site", max: 80, placeholder: "e.g. French, with an Arabic version" },
  { key: "sections", label: "Must-have content", max: 600, type: "textarea",
    placeholder: "e.g. Menu with prices, opening hours, map and address, customer reviews, booking button" },
  { key: "inspiration", label: "Sites or styles you like", max: 300, placeholder: "e.g. apple.com for the calm layout" },
  { key: "avoid", label: "Anything to avoid?", max: 200, placeholder: "e.g. no stock-photo look, no pop-ups" },
];
const briefField = (q, value = "") => q.type === "select"
  ? `<label class="pfield"><span>${esc(q.label)}</span><select class="input" name="b_${q.key}">
      <option value="">Let the team decide</option>${q.options.map(o => `<option value="${esc(o)}" ${o === value ? "selected" : ""}>${esc(o)}</option>`).join("")}
      ${value && !q.options.includes(value) ? `<option selected>${esc(value)}</option>` : ""}</select></label>`
  : q.type === "textarea"
    ? `<label class="pfield wide"><span>${esc(q.label)}</span><textarea class="input" name="b_${q.key}" maxlength="${q.max}" rows="3" placeholder="${esc(q.placeholder)}">${esc(value)}</textarea></label>`
    : `<label class="pfield"><span>${esc(q.label)}</span><input class="input" name="b_${q.key}" maxlength="${q.max}" placeholder="${esc(q.placeholder)}" value="${esc(value)}"></label>`;
/* ---------- a project's brand kit: logo, colours and fonts ----------
   brandFieldsHTML() draws it; bindBrandFields() runs it and returns { values(), state } (state.logoFile, state.removeLogo,
   state.from = the kit to copy the logo from). `live` (the studio) saves each change through `live` callbacks instead. */
const BRAND_FONTS = ["Inter", "Roboto", "Open Sans", "Lato", "Montserrat", "Poppins", "Source Sans 3", "Work Sans", "DM Sans", "Manrope", "Playfair Display",
  "Fraunces", "Lora", "Merriweather", "Cormorant Garamond", "Space Grotesk", "IBM Plex Sans", "Noto Sans Arabic", "Cairo", "Tajawal", "Reem Kufi"];
const BRAND_DEFAULTS = ["#4f5bd5", "#f2b632", "#1fa97a", "#14161f", "#f3f4f8", "#e5484d"];
window.brandFieldsHTML = (sources = []) => `<div class="bk">
    ${sources.length ? `<label class="pfield"><span>Copy from</span><select class="input" data-bk-copy>
      <option value="">Start from scratch</option>${sources.map(k => `<option value="${esc(k.id)}">${esc(k.name)}</option>`).join("")}</select></label>` : ""}
    <div class="bk-grid">
      <div class="bk-side pfield"><span>Logo</span><div class="logo-box" data-bk-logo-box></div>
        <div class="src-files"><button type="button" class="btn btn-ghost btn-sm" data-bk-logo-pick>${icon("image", { size: 14 })}<span data-bk-logo-label>Choose logo…</span></button>
          <button type="button" class="btn btn-ghost btn-sm" data-bk-logo-remove hidden>Remove</button>
          <input type="file" data-bk-logo-file accept="image/png,image/jpeg,image/webp,image/gif" hidden></div>
        <small class="help">PNG, JPG, WebP or GIF, up to 5 MB.</small></div>
      <div class="bk-main">
        <div class="pfield"><span>Colours <em>up to 6 · the first is the main colour</em></span><div class="swatches" data-bk-swatches></div>
          <div class="bk-preview" data-bk-preview aria-hidden="true"></div></div>
        <div class="bk-fonts">
          <label class="pfield"><span>Heading font</span><input class="input" data-bk-heading list="bk-fonts" maxlength="40" placeholder="e.g. Fraunces"></label>
          <label class="pfield"><span>Body font</span><input class="input" data-bk-body list="bk-fonts" maxlength="40" placeholder="e.g. Inter"></label>
        </div>
        <small class="help">Any font from Google Fonts.</small>
        <datalist id="bk-fonts">${BRAND_FONTS.map(f => `<option value="${f}">`).join("")}</datalist>
      </div>
    </div>
    <small class="err" data-bk-err></small>
  </div>`;
window.bindBrandFields = (root, kit = {}, { sources = [], live = null } = {}) => {
  const q = (sel) => root.querySelector(sel);
  const st = { colors: [...(kit.colors || [])], logoUrl: kit.logo || null, logoFile: null, removeLogo: false, from: "" };
  const values = () => ({ colors: st.colors, heading_font: q("[data-bk-heading]").value.trim(), body_font: q("[data-bk-body]").value.trim() });
  const changed = () => { root.dispatchEvent(new Event("input", { bubbles: true })); live?.change?.(values()); };
  const paintLogo = () => {
    q("[data-bk-logo-box]").innerHTML = st.logoUrl ? `<img src="${esc(st.logoUrl)}" alt="Logo">` : `<span>No logo yet</span>`;
    q("[data-bk-logo-remove]").hidden = !st.logoUrl;
    q("[data-bk-logo-label]").textContent = st.logoUrl ? "Replace logo…" : "Choose logo…";
  };
  const paintColors = () => {
    q("[data-bk-swatches]").innerHTML = st.colors.map((c, i) => `<span class="swatch"><input type="color" value="${esc(c)}" data-i="${i}" aria-label="Colour ${i + 1}"><span data-no-i18n>${esc(c)}</span>
      <button type="button" data-del="${i}" aria-label="Remove ${esc(c)}">${icon("x", { size: 12 })}</button></span>`).join("")
      + (st.colors.length < 6 ? `<button type="button" class="btn btn-ghost btn-sm add-color" data-add-color>${icon("plus", { size: 14 })}Add colour</button>` : "");
    q("[data-bk-preview]").innerHTML = st.colors.map(c => `<i style="background:${esc(c)}"></i>`).join("");
    q("[data-bk-preview]").hidden = !st.colors.length;
  };
  q("[data-bk-heading]").value = kit.heading_font || ""; q("[data-bk-body]").value = kit.body_font || "";
  paintLogo(); paintColors();
  q("[data-bk-swatches]").addEventListener("input", (e) => {
    if (e.target.dataset.i === undefined) return;
    st.colors[+e.target.dataset.i] = e.target.value;
    e.target.nextElementSibling.textContent = e.target.value;
    q("[data-bk-preview]").children[+e.target.dataset.i].style.background = e.target.value;
  });
  q("[data-bk-swatches]").addEventListener("change", (e) => { if (e.target.dataset.i !== undefined) changed(); });
  q("[data-bk-swatches]").addEventListener("click", (e) => {
    if (e.target.closest("[data-add-color]")) { st.colors.push(BRAND_DEFAULTS.find(c => !st.colors.includes(c)) || "#888888"); paintColors(); changed(); }
    const del = e.target.closest("[data-del]");
    if (del) { st.colors.splice(+del.dataset.del, 1); paintColors(); changed(); }
  });
  ["[data-bk-heading]", "[data-bk-body]"].forEach(sel => q(sel).addEventListener("change", changed));
  q("[data-bk-logo-pick]").onclick = () => q("[data-bk-logo-file]").click();
  q("[data-bk-logo-file]").onchange = async () => {
    const file = q("[data-bk-logo-file]").files[0];
    q("[data-bk-logo-file]").value = "";
    q("[data-bk-err]").textContent = "";
    if (!file) return;
    if (!ASSET_TYPES.includes(file.type)) { q("[data-bk-err]").textContent = `${file.name}: use PNG, JPG, WebP or GIF`; return; }
    if (file.size > MAX_ASSET_MB * 1048576) { q("[data-bk-err]").textContent = `${file.name} is over 5 MB`; return; }
    if (live) {
      try { const k = await live.logo(`data:${file.type};base64,${await fileBase64(file)}`); st.logoUrl = k.logo; }
      catch (err) { q("[data-bk-err]").textContent = err.message; return; }
    } else {
      if (st.logoUrl?.startsWith("blob:")) URL.revokeObjectURL(st.logoUrl);
      st.logoFile = file; st.logoUrl = URL.createObjectURL(file); st.removeLogo = false; st.from = "";
    }
    paintLogo(); changed();
  };
  q("[data-bk-logo-remove]").onclick = async () => {
    if (live) {
      try { await live.removeLogo(); } catch (err) { q("[data-bk-err]").textContent = err.message; return; }
    }
    st.logoUrl = null; st.logoFile = null; st.removeLogo = true; st.from = "";
    paintLogo(); changed();
  };
  q("[data-bk-copy]")?.addEventListener("change", async (e) => {
    const src = sources.find(k => k.id === e.target.value);
    if (!src) return;
    if (live) {
      try { const k = await live.copy(src.id); Object.assign(st, { colors: [...k.colors], logoUrl: k.logo }); q("[data-bk-heading]").value = k.heading_font; q("[data-bk-body]").value = k.body_font; }
      catch (err) { q("[data-bk-err]").textContent = err.message; return; }
    } else {
      st.colors = [...(src.colors || [])]; q("[data-bk-heading]").value = src.heading_font || ""; q("[data-bk-body]").value = src.body_font || "";
      st.from = src.logo ? src.id : ""; st.logoFile = null; st.logoUrl = src.logo || null;
    }
    paintColors(); paintLogo(); changed();
    e.target.value = "";
  });
  return { values, state: st };
};

/* The project form, in a dialog (edit details) or on a page (/projects/new). `d` is the element to fill; onSubmit gets the
   checked answers. Returns the images chosen so far, so the caller can release their previews. */
window.fillProjectForm = (d, { title = "New project", ok = "Create project", name = "", description = "", pages = ["Home"], note = "", cancellable = true, allowImport = false, brief = {}, askAssets = false, cancelHref = "", heading = true, brandSources = null } = {}, onSubmit) => {
    d.innerHTML = `
      <form method="dialog" novalidate>
        ${heading ? `<h3>${esc(title)}</h3>
        <p>${esc(note || "Every project has a name and a short description. The agents read both, so be specific.")}</p>` : ""}
        <div class="pf-main">
        <label class="pfield"><span>Name</span>
          <input class="input" name="name" maxlength="80" required placeholder="e.g. Bean There coffee shop" value="${esc(name)}">
          <small class="err" data-for="name"></small></label>
        <label class="pfield"><span>Description <em data-count>0/300</em></span>
          <textarea class="input" name="description" maxlength="300" rows="3" required
            placeholder="e.g. A cozy neighbourhood café in Lyon. One page with the menu, opening hours and a booking button.">${esc(description)}</textarea>
          <small class="err" data-for="description"></small></label>
        <label class="pfield" data-pages><span>Pages <em>one per line · the first is the home page</em></span>
          <textarea class="input" name="pages" rows="3" placeholder="Home&#10;About&#10;Contact">${esc((pages || ["Home"]).join("\n"))}</textarea>
          <small class="err" data-for="pages"></small></label>
        ${allowImport ? `<div class="pfield" data-import><span>Existing site <em>optional · HTML, CSS, JS, Markdown and assets</em></span>
          <div class="src-files">
            <button type="button" class="btn btn-ghost btn-sm" data-choose="folder">${icon("folder", { size: 14 })}Choose folder…</button>
            <button type="button" class="btn btn-ghost btn-sm" data-choose="zip">${icon("download", { size: 14 })}Choose .zip…</button>
            <button type="button" class="btn btn-ghost btn-sm" data-clear-source hidden>${icon("x", { size: 14 })}Remove</button>
            <input type="file" data-folder webkitdirectory multiple hidden>
            <input type="file" data-zip accept=".zip,application/zip" hidden>
          </div>
          <small class="src-summary" data-summary>Leave empty to start from a blank site. Needs an index.html at the top; other files (server code, configs, .git…) are left out.</small>
          <small class="err" data-for="source"></small></div>` : ""}

        </div>
        <div class="pf-more">
        ${brandSources ? `<fieldset class="pgroup" data-brand><legend>Brand kit <em>optional · this project's logo, colours and fonts</em></legend>
          ${brandFieldsHTML(brandSources)}</fieldset>` : ""}
        <fieldset class="pgroup"><legend>About your site <em>optional · the more you say, the closer the first build</em></legend>
          <div class="pgrid">${BRIEF_QUESTIONS.map(q => briefField(q, (brief || {})[q.key] || "")).join("")}</div>
        </fieldset>
        ${askAssets ? `<fieldset class="pgroup"><legend>Your images <em>optional · PNG, JPG, WebP or GIF, up to ${MAX_ASSET_MB} MB each</em></legend>
          <div class="pgrid">
            <div class="pfield"><span>Photos <em>products, team, place…</em></span>
              <div class="src-files"><button type="button" class="btn btn-ghost btn-sm" data-pick="photo">${icon("image", { size: 14 })}Add photos…</button>
                <input type="file" data-asset="photo" accept="${ASSET_TYPES.join(",")}" multiple hidden></div></div>
          </div>
          <ul class="asset-list" data-assets></ul>
          <small class="err asset-err" data-for="assets"></small>
        </fieldset>` : ""}
        </div>
        <div class="row">
          ${cancelHref ? `<a class="btn btn-ghost" href="${esc(cancelHref)}">Cancel</a>` : cancellable ? `<button class="btn btn-ghost" value="cancel" formnovalidate>Cancel</button>` : ""}
          <button class="btn btn-dark" value="ok">${esc(ok)}</button>
        </div>
      </form>`;
    const form = d.querySelector("form"), f = form.elements;
    const brandUI = brandSources ? bindBrandFields(d.querySelector("[data-brand]"), {}, { sources: brandSources }) : null;
    const brandOf = () => brandUI ? { brand: brandUI.values(), brand_logo: brandUI.state.logoFile, brand_from: brandUI.state.from } : {};
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
    // ---- start from existing files
    let mode = "blank", source = null;
    const srcErr = (t) => { const el = d.querySelector('[data-for="source"]'); if (el) el.textContent = t; };
    const summary = (t) => d.querySelector("[data-summary]").textContent = t;
    const extOf = (p) => (p.split(".").pop() || "").toLowerCase();
    const junk = (p) => p.split("/").some(seg => seg.startsWith(".") || seg === "node_modules" || seg === "__MACOSX");
    if (allowImport) {
      d.querySelector('[data-choose="folder"]').addEventListener("click", () => d.querySelector("[data-folder]").click());
      d.querySelector('[data-choose="zip"]').addEventListener("click", () => d.querySelector("[data-zip]").click());
      d.querySelector("[data-folder]").addEventListener("change", (e) => {
        const all = [...e.target.files].map(file => ({ path: file.webkitRelativePath || file.name, file }));
        const keep = all.filter(f => !junk(f.path) && SITE_EXT.includes(extOf(f.path)));
        const size = keep.reduce((n, f) => n + f.file.size, 0);
        srcErr(""); source = null;
        if (!keep.length) return srcErr("No HTML, CSS, JS, Markdown or asset files in that folder.");
        if (!keep.some(f => /\.html?$/i.test(f.path))) return srcErr("That folder has no .html page.");
        if (size > MAX_SITE_MB * 1024 * 1024) return srcErr(`Those files add up to ${(size / 1048576).toFixed(1)} MB; the limit is ${MAX_SITE_MB} MB.`);
        source = { files: keep };
        const root = all[0]?.path.split("/")[0] || "folder";
        summary(`${root}: ${keep.length} file${keep.length === 1 ? "" : "s"} (${(size / 1048576).toFixed(1)} MB)` +
          (all.length > keep.length ? ` · ${all.length - keep.length} other file${all.length - keep.length === 1 ? "" : "s"} left out` : ""));
        if (!f.name.value.trim()) f.name.value = root.slice(0, 80);
      });
      d.querySelector("[data-zip]").addEventListener("change", (e) => {
        const file = e.target.files[0];
        srcErr(""); source = null;
        if (!file) return;
        if (file.size > MAX_SITE_MB * 1024 * 1024) return srcErr(`That .zip is ${(file.size / 1048576).toFixed(1)} MB; the limit is ${MAX_SITE_MB} MB.`);
        source = { zip: file };
        summary(`${file.name} (${(file.size / 1048576).toFixed(1)} MB) · only HTML, CSS, JS, Markdown and assets are kept`);
        if (!f.name.value.trim()) f.name.value = file.name.replace(/\.zip$/i, "").slice(0, 80);
      });
      /* With files chosen, the pages come from them, so the list of pages steps aside. */
      const blankHint = d.querySelector("[data-summary]").textContent;
      const refreshSource = () => {
        mode = source ? "files" : "blank";
        d.querySelector("[data-clear-source]").hidden = !source;
        f.pages.disabled = !!source;
        d.querySelector("[data-pages]").classList.toggle("off", !!source);
        if (!source) summary(blankHint);
      };
      d.querySelector("[data-folder]").addEventListener("change", refreshSource);
      d.querySelector("[data-zip]").addEventListener("change", refreshSource);
      d.querySelector("[data-clear-source]").addEventListener("click", () => {
        source = null; srcErr("");
        d.querySelector("[data-folder]").value = ""; d.querySelector("[data-zip]").value = "";
        refreshSource();
      });
    }

    // ---- the user's own images (logo + photos), uploaded once the project exists
    let assets = [];   // {file, kind, note, url}
    const assetErr = (t) => { const el = d.querySelector('[data-for="assets"]'); if (el) el.textContent = t; };
    const renderAssets = () => {
      d.querySelector("[data-assets]").innerHTML = assets.map((a, i) => `<li><img src="${a.url}" alt="" width="40" height="40">
        <span class="asset-kind">${a.kind === "logo" ? "Logo" : "Photo"}</span>
        <input class="input" data-note="${i}" maxlength="120" placeholder="${a.kind === "logo" ? "Optional note" : "What it shows, e.g. our terrace at sunset"}" value="${esc(a.note)}" aria-label="Note for ${esc(a.file.name)}">
        <button type="button" class="icon-btn plain" data-remove="${i}" aria-label="Remove ${esc(a.file.name)}">${icon("x", { size: 14 })}</button></li>`).join("");
    };
    if (askAssets) {
      d.querySelectorAll("[data-pick]").forEach(b => b.addEventListener("click", () => d.querySelector(`[data-asset="${b.dataset.pick}"]`).click()));
      d.querySelectorAll("[data-asset]").forEach(input => input.addEventListener("change", () => {
        const kind = input.dataset.asset;
        assetErr("");
        for (const file of input.files) {
          if (!ASSET_TYPES.includes(file.type)) { assetErr(`${file.name} isn't a PNG, JPG, WebP or GIF image.`); continue; }
          if (file.size > MAX_ASSET_MB * 1048576) { assetErr(`${file.name} is ${(file.size / 1048576).toFixed(1)} MB; the limit is ${MAX_ASSET_MB} MB.`); continue; }
          if (kind === "logo") assets = assets.filter(a => { if (a.kind === "logo") URL.revokeObjectURL(a.url); return a.kind !== "logo"; });
          if (assets.length >= MAX_ASSETS) { assetErr(`Up to ${MAX_ASSETS} images.`); break; }
          const entry = { file, kind, note: "", url: URL.createObjectURL(file) };
          kind === "logo" ? assets.unshift(entry) : assets.push(entry);
        }
        input.value = "";
        renderAssets();
      }));
      d.querySelector("[data-assets]").addEventListener("input", (e) => { if (e.target.dataset.note) assets[+e.target.dataset.note].note = e.target.value; });
      d.querySelector("[data-assets]").addEventListener("click", (e) => {
        const b = e.target.closest("[data-remove]");
        if (!b) return;
        URL.revokeObjectURL(assets[+b.dataset.remove].url);
        assets.splice(+b.dataset.remove, 1); renderAssets();
      });
    }
    const briefOf = () => Object.fromEntries(BRIEF_QUESTIONS.map(q => [q.key, f[`b_${q.key}`].value.trim()]).filter(([, v]) => v));

    form.addEventListener("submit", (e) => {
      if (e.submitter?.value === "cancel") return;
      if (!check()) { e.preventDefault(); return; }
      if (mode === "files") {
        if (!source) { e.preventDefault(); srcErr("Choose a folder or a .zip with your site."); return; }
        onSubmit({ name: f.name.value.trim(), description: f.description.value.trim(), brief: briefOf(), source, assets, ...brandOf() }, e);
        return;
      }
      const pageList = f.pages.value.split("\n").map(x => x.trim()).filter(Boolean);
      if (pageList.length > 12) { e.preventDefault(); d.querySelector('[data-for="pages"]').textContent = "Up to 12 pages."; return; }
      onSubmit({ name: f.name.value.trim(), description: f.description.value.trim(), pages: pageList.length ? pageList : ["Home"], brief: briefOf(), assets, ...brandOf() }, e);
    });
    f.description.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); form.requestSubmit(form.querySelector('[value="ok"]')); }
    });
    return { focus: () => (f.name.value ? f.description : f.name).focus(), images: () => assets };
};

window.projectDialog = (opts = {}) => new Promise((resolve) => {
  const d = document.createElement("dialog");
  d.className = "modal project-modal";
  document.body.append(d);
  let result = null;
  const form = fillProjectForm(d, opts, (answers) => { result = answers; });
  const cancellable = opts.cancellable !== false;
  if (!cancellable) d.addEventListener("cancel", (e) => e.preventDefault());
  d.addEventListener("click", (e) => { if (cancellable && e.target === d) d.close(); });
  d.addEventListener("close", () => {
    if (!result) form.images().forEach(a => URL.revokeObjectURL(a.url));
    resolve(result); d.remove();
  });
  d.showModal();
  form.focus();
});

const fileBase64 = (file) => new Promise((resolve, reject) => {
  const fr = new FileReader();
  fr.onload = () => resolve(String(fr.result).split(",")[1] || "");
  fr.onerror = () => reject(new Error(`Couldn't read ${file.name}.`));
  fr.readAsDataURL(file);
});

/* The New project button: its page, once the account is ready. */
window.newProject = async () => {
  const who = await getMe();
  const thenNew = `?next=${encodeURIComponent("/projects/new")}`;
  location.href = !who ? `/register${thenNew}` : !who.confirmed ? `/confirm${thenNew}` : "/projects/new";
};

/* Create the project from the form's answers (a blank site or existing files, plus images) and open it. */
window.createProject = async (details) => {
  const { assets = [], brand_logo = null, brand_from = "", ...fields } = details;
  try {
    let p;
    if (details.source) {
      toast("Importing your site…");
      const { name, description, brief, source, brand } = details;
      const body = source.zip
        ? { name, description, brief, brand, zip: await fileBase64(source.zip) }
        : { name, description, brief, brand, files: await Promise.all(source.files.map(async x => ({ path: x.path, data: await fileBase64(x.file) }))) };
      p = await api("/api/projects/import", { method: "POST", body: JSON.stringify(body), timeout: 120000 });
      if (p.skipped?.length) sessionStorage.setItem("atelier-import-note", `Left out ${p.skipped.length} file${p.skipped.length === 1 ? "" : "s"} that aren't HTML, CSS, JS, Markdown or assets.`);
    } else {
      p = await api("/api/projects", { method: "POST", body: JSON.stringify(fields) });
    }
    if (assets.length) toast(`Uploading ${assets.length} image${assets.length === 1 ? "" : "s"}…`);
    const failed = [];
    for (const a of assets) {   // one at a time, so each request stays under the body limit
      try {
        const data = `data:${a.file.type};base64,${await fileBase64(a.file)}`;
        await api(`/api/projects/${p.id}/assets`, { method: "POST", timeout: 60000,
          body: JSON.stringify({ name: a.file.name, data, kind: a.kind, note: a.note.trim() }) });
      } catch (err) { failed.push(`${a.file.name}: ${err.message}`); }
      URL.revokeObjectURL(a.url);
    }
    if (failed.length) {
      const prev = sessionStorage.getItem("atelier-import-note");
      sessionStorage.setItem("atelier-import-note", (prev ? prev + " " : "") + `Some images weren't added: ${failed.join(" · ")}`);
    }
    try {                // the brand kit's logo: the one chosen in the form, or the copied kit's
      if (brand_logo) await api(`/api/projects/${p.id}/brand/logo`, { method: "POST", timeout: 60000,
        body: JSON.stringify({ data: `data:${brand_logo.type};base64,${await fileBase64(brand_logo)}` }) });
      else if (brand_from) await api(`/api/projects/${p.id}/brand/copy`, { method: "POST", body: JSON.stringify({ from: brand_from, logo_only: true }) });
    } catch (err) { sessionStorage.setItem("atelier-import-note", `The brand kit's logo wasn't added: ${err.message}`); }
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
            <option value="rename" ${policy === "rename" ? "selected" : ""}>Keep both: import as a copy</option>
            <option value="replace" ${policy === "replace" ? "selected" : ""}>Replace mine with the imported one</option>
            <option value="skip" ${policy === "skip" ? "selected" : ""}>Keep mine: skip it</option></select></label>
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
    if (authState() !== "member") return [];
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
    ...(authState() === "guest" ? [
      { open: true, group: "Account", icon: "user", title: "Log in", run: () => location.href = "/login" },
      { open: true, group: "Account", icon: "plus", title: "Create an account", run: () => location.href = "/register" }] : []),
    ...(authState() === "unconfirmed" ? [
      { open: true, group: "Account", icon: "check", title: "Confirm your email", run: () => location.href = "/confirm" }] : []),
    ...(document.documentElement.dataset.admin ? [
      { group: "Go to", icon: "user", title: "Users", run: () => location.href = "/users" },
      { group: "Go to", icon: "chart", title: "Analytics", run: () => location.href = "/analytics" }] : []),
    { open: true, group: "Go to", icon: "home", title: "Home", run: () => location.href = "/" },
    { open: true, group: "Go to", icon: "briefcase", title: "Hire agents", run: () => location.href = "/hire" },
    { group: "Go to", icon: "grid", title: "Projects", run: () => location.href = "/projects" },
    { group: "Go to", icon: "user", title: "Profile and password", run: () => location.href = "/profile" },
    { group: "Go to", icon: "users", title: "My agents", run: () => location.href = "/agents" },
    { group: "Go to", icon: "layers", title: "My teams", run: () => location.href = "/teams" },
    { group: "Go to", icon: "book", title: "Guide", run: () => location.href = "/guide" },
    { group: "Go to", icon: "file", title: "Shared rules", run: () => location.href = "/agents/rules" },
    { group: "Create", icon: "plus", title: "New project", run: () => newProject() },
    { group: "Create", icon: "plus", title: "New agent", run: () => location.href = "/agents/new" },
    { group: "Create", icon: "plus", title: "New team", run: () => location.href = "/teams/new" },
    { group: "Agents & teams", icon: "download", title: "Export all agents and teams (.zip)", run: () => location.href = "/api/export" },
    { group: "Agents & teams", icon: "file-plus", title: "Import agents or teams…", run: async () => { if (await importFlow()) location.reload(); } },
    { open: true, group: "Appearance", icon: "sun", title: "Theme: light", run: () => setTheme("light") },
    { open: true, group: "Appearance", icon: "moon", title: "Theme: dark", run: () => setTheme("dark") },
    { open: true, group: "Appearance", icon: "monitor", title: "Theme: follow the system", run: () => setTheme("system") },
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
    dlg.innerHTML = `<div class="pal-input">${icon("search", { size: 18 })}<input type="text" placeholder="${authState() === "member" ? "Search projects, agents, teams and actions…" : "Search pages and actions…"}"
        aria-label="Search" autocomplete="off" spellcheck="false"><kbd>Esc</kbd></div>
      <div class="pal-list" role="listbox"></div>
      <div class="pal-foot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>Ctrl</kbd>+<kbd>K</kbd> anywhere</span></div>`;
    document.body.append(dlg);
    const input = dlg.querySelector("input"), box = dlg.querySelector(".pal-list");
    let all = [...pageCommands(), ...staticCommands().filter(c => c.open || authState() === "member")];
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
    const site = document.querySelector("header.top .wrap");
    const make = () => {
      const wrap = document.createElement("span");
      wrap.className = "hdr-tools";
      wrap.innerHTML = `<button class="icon-btn pal-btn" data-show="member" type="button" title="Search and commands (${mac ? "⌘" : "Ctrl+"}K)" aria-label="Search and commands">${icon("search", { size: 17 })}</button>
        <button class="icon-btn" type="button" data-theme-btn data-show="guest unconfirmed" aria-label="Change theme">${icon(themeIcon(), { size: 17 })}</button>`;
      wrap.querySelector(".pal-btn").onclick = open;
      wrap.querySelector("[data-theme-btn]").onclick = () => cycleTheme();
      wrap.querySelector("[data-theme-btn]").title = themeTitle();
      getMe().then(user => {
        if (!user?.confirmed) wrap.prepend(languageButton());   // no profile page yet: the language button is here
        if (!user) return;
        wrap.append(accountButton(user, { compact: !site }));
        const nav = document.querySelector("header.top .links");
        if (user.confirmed && nav && !nav.querySelector('[href="/hire"]')) {     // the marketplace, right after Projects
          const hire = `<a href="/hire" data-show="member" class="${location.pathname.startsWith("/hire") ? "on" : ""}">${icon("briefcase", { size: 16 })}Hire</a>`;
          const first = nav.querySelector('[href="/projects"]');
          first ? first.insertAdjacentHTML("afterend", hire) : nav.insertAdjacentHTML("afterbegin", hire);
        }
        if (user.role === "admin") {
          document.documentElement.dataset.admin = "1";
          const links = document.querySelector("header.top .links");
          if (links && !links.querySelector('[href="/users"]')) links.insertAdjacentHTML("beforeend",
            `<a href="/users" data-show="member" class="${location.pathname === "/users" ? "on" : ""}">${icon("user", { size: 16 })}Users</a>
             <a href="/analytics" data-show="member" class="${location.pathname === "/analytics" ? "on" : ""}">${icon("chart", { size: 16 })}Analytics</a>`);
        }
      });
      return wrap;
    };
    const main = document.querySelector("main");     // keyboard users can jump past the header
    if (main) {
      main.id ||= "main";
      main.tabIndex = -1;
      document.body.insertAdjacentHTML("afterbegin", `<a class="skip-link" href="#${main.id}">Skip to content</a>`);
    }
    cookieNotice();
    try {
      const note = sessionStorage.getItem("atelier-toast");
      if (note) { sessionStorage.removeItem("atelier-toast"); toast(note); }
    } catch {}
    if (site) site.insertBefore(make(), site.lastElementChild);
    // not under the one-screen New project form
    if (site && location.pathname !== "/projects/new" && !document.querySelector("footer.site-footer")) siteFooter();
    const studio = document.querySelector(".chat .bar");
    if (studio) studio.insertBefore(make(), document.getElementById("menu"));
  });
})();

/* ---------- the footer, on every page with the top navigation ---------- */
function siteFooter() {
  const link = (href, text, show = "") => `<li${show ? ` data-show="${show}"` : ""}><a href="${href}">${text}</a></li>`;
  const onLanding = location.pathname === "/";
  const f = document.createElement("footer");
  f.className = "site-footer";
  f.innerHTML = `<div class="wrap">
    <div class="foot-brand">
      <a class="logo" href="/"><span class="mark">${icon("logo", { size: 18 })}</span>Agents Atelier</a>
      <p>A team of AI specialists that builds your website in plain HTML, CSS and JavaScript, while you watch.</p>
      <div class="foot-langs" role="group" aria-label="Language" data-no-i18n>${LANGS.map(([k, name]) =>
        `<button type="button" class="foot-lang ${k === getLang() ? "on" : ""}" data-lang-pick="${k}" lang="${k}" aria-pressed="${k === getLang()}">${name}</button>`).join("")}</div>
    </div>
    <nav class="foot-cols" aria-label="Footer">
      <div><h2>Product</h2><ul>
        ${link(onLanding ? "#how" : "/#how", "How it works", "guest unconfirmed")}
        ${link(onLanding ? "#examples" : "/#examples", "Examples", "guest unconfirmed")}
        ${link(onLanding ? "#faq" : "/#faq", "FAQ", "guest unconfirmed")}
        ${link("/hire", "Hire agents", "member")}${link("/projects", "Projects", "member")}${link("/agents", "My agents", "member")}${link("/teams", "My teams", "member")}${link("/guide", "Guide", "member")}
      </ul></div>
      <div><h2>Account</h2><ul>
        ${link("/register", "Sign up", "guest")}${link("/login", "Log in", "guest")}${link("/forgot", "Forgot your password?", "guest")}
        ${link("/confirm", "Confirm your email", "unconfirmed")}
        ${link("/profile", "Profile", "member")}${link("/profile#data", "Download my data", "member")}
      </ul></div>
      <div><h2>Legal</h2><ul>${link("/terms", "Terms of use")}${link("/privacy", "Privacy policy")}
        <li><button type="button" class="foot-cookies">Cookie notice</button></li></ul></div>
    </nav>
  </div>
  <div class="wrap foot-bottom"><span>© ${new Date().getFullYear()} <span class="foot-owner" data-no-i18n>Agents Atelier</span></span><span>Sites built here belong to the people who made them.</span></div>`;
  f.querySelectorAll("[data-lang-pick]").forEach(b => { b.onclick = () => pickLanguage(b.dataset.langPick); });
  f.querySelector(".foot-cookies").onclick = () => { try { localStorage.removeItem("atelier-cookies-ok"); } catch {} document.querySelector(".cookie-note")?.remove(); cookieNotice(); };
  document.body.append(f);
  fetch("/api/auth/legal").then(r => r.ok ? r.json() : null).then(info => { if (info?.name) f.querySelector(".foot-owner").textContent = info.name; }).catch(() => {});
}

/* ---------- language: a small menu with the three languages ---------- */
window.pickLanguage = (next) => {
  if (next === getLang()) return;
  Promise.resolve(window.savePreferences?.({ language: next })).finally(() => setLang(next));
};
window.languageButton = () => {
  const box = document.createElement("span");
  box.className = "lang-pick";
  const cur = LANGS.find(l => l[0] === getLang()) || LANGS[0];
  box.innerHTML = `<button class="icon-btn lang-btn" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="Language: ${cur[1]}" title="${cur[1]}" data-no-i18n>${cur[2]}</button>
    <span class="lang-menu" role="menu" hidden data-no-i18n>${LANGS.map(([k, name]) =>
      `<button type="button" role="menuitemradio" aria-checked="${k === cur[0]}" lang="${k}" data-lang-pick="${k}">${name}</button>`).join("")}</span>`;
  const btn = box.querySelector(".lang-btn"), menu = box.querySelector(".lang-menu");
  const close = () => { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); };
  btn.onclick = (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; btn.setAttribute("aria-expanded", String(!menu.hidden)); if (!menu.hidden) menu.querySelector("[aria-checked=true]").focus(); };
  menu.onclick = (e) => { const b = e.target.closest("[data-lang-pick]"); if (b) { close(); pickLanguage(b.dataset.langPick); } };
  document.addEventListener("click", (e) => { if (!box.contains(e.target)) close(); });
  box.addEventListener("keydown", (e) => { if (e.key === "Escape") { close(); btn.focus(); } });
  return box;
};

/* ---------- the cookie notice: shown once, until dismissed ---------- */
function cookieNotice() {
  try { if (localStorage.getItem("atelier-cookies-ok")) return; } catch { return; }
  const box = document.createElement("div");
  box.className = "cookie-note";
  box.setAttribute("role", "region");
  box.setAttribute("aria-label", "Cookies");
  box.innerHTML = `<p>We use one cookie to keep you logged in, and your browser remembers your theme and language. No advertising or tracking cookies.</p>
    <div><a href="/privacy">Privacy policy</a><button class="btn btn-dark btn-sm" type="button">OK</button></div>`;
  box.querySelector("button").onclick = () => { try { localStorage.setItem("atelier-cookies-ok", "1"); } catch {} box.remove(); };
  document.body.append(box);
}

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
    // MutationObserver callbacks run later: the node may already have been removed again (e.g. folded chat history)
    if (seen.has(el) || !el.isConnected || !el.parentElement) return;
    if (el.closest(".reveal:not(.in)") || el.classList.contains("reveal")) return;
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
  // purely decorative: a problem here must never break the page
  const safeScan = (root) => { try { scan(root); } catch (err) { console.warn("Skipped an entrance animation", err); } };
  const mo = new MutationObserver(list => list.forEach(m => m.addedNodes.forEach(safeScan)));
  document.addEventListener("DOMContentLoaded", () => {
    safeScan(document.body);
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
