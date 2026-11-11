/* Shared bits of the marketplace pages (catalogue, profiles, job post). Needs ui.js and icons.js. */
window.CATEGORY = {
  planning: "Planning", writing: "Writing", design: "Design", code: "Code", growth: "Growth", quality: "Quality",
  build: "Build something new", improve: "Improve a site you have", add: "Add one thing",
};
window.CATEGORY_ICON = {
  planning: "compass", writing: "type", design: "palette", code: "code", growth: "chart", quality: "check",
  build: "sparkles", improve: "refresh", add: "plus",
};

/* "92% liked · 12 ratings", or "No ratings yet" */
window.ratingText = (r) => r && r.count
  ? `<span class="good" title="${r.up} up · ${r.down} down">${icon("thumbs-up", { size: 13 })}${r.percent}%</span><span>${r.count} rating${r.count === 1 ? "" : "s"}</span>`
  : `<span>${icon("thumbs-up", { size: 13 })}No ratings yet</span>`;

/* Official agents and teams are written by us and translated; a member's own name and description stay as written. */
window.userText = (item) => item && !item.official ? "data-no-i18n" : "";

window.timeText = (sec) => sec == null ? "–" : sec < 60 ? `${Math.max(1, Math.round(sec))}s` : sec < 3600 ? `${Math.round(sec / 60)} min` : `${(sec / 3600).toFixed(1)} h`;
window.costText = (usd) => usd == null ? "–" : usd >= 0.1 ? `$${usd.toFixed(2)}` : money(usd);

window.sourceBadge = (item) => item.official
  ? `<span class="badge official">Official</span>`
  : item.listing && item.listing !== "listed"
    ? `<span class="badge ${esc(item.listing)}">${{ pending: "Waiting for review", rejected: "Not approved", private: "Private" }[item.listing] || esc(item.listing)}</span>`
    : `<span class="badge community">Community</span>`;

/* Hire a community agent or team: it's copied into your own. Returns the server's answer. */
window.hire = async (kind, pk, name) => {
  const r = await api(`/api/market/${kind === "team" ? "teams" : "agents"}/${pk}/hire`, { method: "POST" });
  toast(r.already ? `You already have ${name}` : kind === "team" ? `${name} hired: it's in your teams` : `${name} hired: it's in your agents`);
  return r;
};

/* Choosing for a project: the studio's "Add agents" and "Choose a team" open the marketplace with the project noted in
   sessionStorage. Then "Start a job" sends the agent or team straight back to that project, with no project picker. */
window.JOB_FOR = (() => { try { const j = JSON.parse(sessionStorage.getItem("atelier-job-for") || "null"); return j && j.id ? j : null; } catch { return null; } })();
const onTeam = (kind, id) => kind !== "team" && !!JOB_FOR?.agents?.includes(id);
function sendToProject(kind, id) {
  try {
    sessionStorage.setItem("atelier-start-for", JOB_FOR.id);
    if (kind === "team") sessionStorage.setItem("atelier-start-team", id);
    else sessionStorage.setItem("atelier-start-add", JSON.stringify([id]));
  } catch {}
  location.href = `/p/${JOB_FOR.id}`;
}
function jobBanner() {
  if (!JOB_FOR || document.querySelector(".job-for")) return;
  const main = document.querySelector("main"); if (!main) return;
  main.insertAdjacentHTML("beforebegin", `<div class="wrap job-wrap"><div class="job-for" role="status">
    <span class="ico">${icon("briefcase", { size: 18 })}</span>
    <span class="txt"><span>Choosing for</span> <b data-no-i18n>${esc(JOB_FOR.name || "your project")}</b><small>Add an agent to its team, or use a whole team. It's ready in the studio for your next prompt.</small></span>
    <a class="btn btn-ghost btn-sm" href="/p/${esc(JOB_FOR.id)}">${icon("arrow-right", { size: 14, cls: "flip" })}Back to the studio</a>
    <button class="icon-btn plain" type="button" data-job-for-off title="Stop choosing for this project" aria-label="Stop choosing for this project">${icon("x", { size: 16 })}</button></div></div>`);
}
if (JOB_FOR) {
  const css = document.createElement("style");
  css.textContent = `.job-wrap { padding-top: 20px; }
    .job-for { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 12px 16px; margin: 0; border-radius: var(--r);
      background: var(--paper); box-shadow: var(--ring); }
    .job-for .ico { width: 36px; height: 36px; border-radius: var(--r); display: grid; place-items: center; background: var(--ice, var(--linen)); color: var(--sky, var(--ink)); flex: none; }
    .job-for .txt { flex: 1 1 260px; min-width: 0; font-size: 14px; color: var(--ink); }
    .job-for .txt small { display: block; font-size: 13px; color: var(--stone); margin-top: 2px; }`;
  document.head.append(css);
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", jobBanner) : jobBanner();
  document.addEventListener("click", (e) => {
    if (!e.target.closest("[data-job-for-off]")) return;
    try { sessionStorage.removeItem("atelier-job-for"); } catch {}
    location.reload();
  });
}

/* The action buttons of a catalogue card or a profile.
   Start a job: pick one of your projects and the agent (or team) joins it. Hire: copy a community one, then the same. */
window.hireButtons = (kind, item, { big = false } = {}) => {
  const size = big ? "" : " btn-sm";
  const start = (id) => onTeam(kind, id)
    ? `<span class="badge listed">${icon("check", { size: 12 })}In the team</span>`
    : `<button class="btn btn-dark${size}" type="button" data-start-job="${kind}" data-id="${esc(id)}" data-name="${esc(item.name)}">${JOB_FOR
        ? `${icon("plus", { size: 15 })}${kind === "team" ? "Use this team" : "Add to the team"}` : `${icon("briefcase", { size: 15 })}Start a job`}</button>`;
  if (item.mine) return start(item.id);
  if (item.official) return start(item.id);
  if (item.hired_as) return `<span class="badge listed">${icon("check", { size: 12 })}Hired</span>${start(item.hired_as)}`;
  return `<button class="btn btn-dark${size}" type="button" data-hire="${kind}" data-pk="${item.pk}" data-name="${esc(item.name)}">${icon("plus", { size: 15 })}Hire</button>`;
};

/* "Start a job with X": choose one of your projects. X joins its team (a team replaces the selection) in the studio. */
window.startJobDialog = (kind, id, name) => JOB_FOR ? sendToProject(kind, id) : new Promise((resolve) => {
  const d = document.createElement("dialog");
  d.className = "modal project-modal job-pick";
  d.innerHTML = `<form method="dialog" novalidate>
    <h3><span>Start a job with</span> <span data-no-i18n>${esc(name)}</span></h3>
    <p>${kind === "team" ? "Pick the project this team should work on. It becomes the project's team, ready for your next prompt." : "Pick the project this agent should work on. It joins the project's team, ready for your next prompt."}</p>
    <label class="search"><i data-icon="search" data-size="16"></i><input class="input" name="q" placeholder="Search your projects" autocomplete="off"></label>
    <div class="jp-list" role="radiogroup" aria-label="Your projects"><p class="jp-note">Loading…</p></div>
    <nav class="pager compact jp-pager" aria-label="Pages of your projects"></nav>
    <div class="row"><button class="btn btn-ghost" type="button" data-new-with>${icon("plus", { size: 15 })}Start a new project instead</button><span class="spacer"></span>
      <button class="btn btn-ghost" value="cancel" formnovalidate>Cancel</button>
      <button class="btn btn-dark" value="ok" disabled>${icon("arrow-right", { size: 15 })}Open the project</button></div>
  </form>`;
  document.body.append(d);
  const list = d.querySelector(".jp-list"), pager = d.querySelector(".jp-pager"), ok = d.querySelector('[value="ok"]'), q = d.querySelector('[name="q"]');
  let page = 1, picked = null, timer;
  async function load() {
    try {
      const r = await api(`/api/projects?mine=true&page=${page}&per_page=6&sort=updated&q=${encodeURIComponent(q.value.trim())}`);
      list.innerHTML = r.items.length ? r.items.map(p => `<label class="jp-item ${p.busy ? "busy" : ""}">
          <input type="radio" name="pid" value="${esc(p.id)}" ${p.busy ? "disabled" : ""} ${picked === p.id ? "checked" : ""}>
          <span class="jp-shot">${p.has_preview ? `<iframe data-src="/work/${p.id}/index.html" tabindex="-1" title="" aria-hidden="true" sandbox=""></iframe>` : icon("folder", { size: 18 })}</span>
          <span class="jp-text"><b data-no-i18n>${esc(p.name)}</b><small data-no-i18n>${esc(p.description || "")}</small></span>
          <span class="jp-meta">${p.busy ? `<span class="badge live">Working</span>` : esc(ago(p.updated))}</span></label>`).join("")
        : q.value.trim() ? `<p class="jp-note">No project matches.</p>`
        : `<div class="jp-empty"><p>You don't have a project yet. Start one: it opens with this ${kind === "team" ? "team" : "agent"} ready.</p>
             <button class="btn btn-dark btn-sm" type="button" data-new-with>${icon("plus", { size: 15 })}New project</button></div>`;
      pager.innerHTML = pagerHTML({ page: r.page, pages: r.pages, total: r.total, perPage: 6 }, { sizes: [6], noun: "projects" });
      lazyEach(list);
    } catch (err) { list.innerHTML = `<p class="jp-note">${esc(err.message)}</p>`; }
  }
  // a new project instead: the studio applies the agent or team when that new project first opens
  d.addEventListener("click", (e) => {
    if (!e.target.closest("[data-new-with]")) return;
    try {
      if (kind === "team") sessionStorage.setItem("atelier-start-team", id);
      else sessionStorage.setItem("atelier-start-add", JSON.stringify([id]));
    } catch {}
    location.href = "/projects/new";
  });
  list.addEventListener("change", (e) => { if (e.target.name === "pid") { picked = e.target.value; ok.disabled = false; } });
  list.addEventListener("dblclick", (e) => { if (e.target.closest(".jp-item:not(.busy)")) d.querySelector("form").requestSubmit(ok); });
  pager.addEventListener("click", (e) => { const b = e.target.closest("[data-page]"); if (b && !b.disabled) { page = +b.dataset.page; load(); } });
  q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { page = 1; load(); }, 200); });
  let result = null;
  d.querySelector("form").addEventListener("submit", (e) => { if (e.submitter?.value === "ok" && picked) result = picked; });
  d.addEventListener("click", (e) => { if (e.target === d) d.close(); });
  d.addEventListener("close", () => {
    d.remove();
    if (result) {
      try {
        sessionStorage.setItem("atelier-start-for", result);
        if (kind === "team") sessionStorage.setItem("atelier-start-team", id);
        else sessionStorage.setItem("atelier-start-add", JSON.stringify([id]));
      } catch {}
      location.href = `/p/${result}`;
    }
    resolve(result);
  });
  d.showModal(); q.focus(); load();
});

/* Clicks on Hire / Start a job anywhere on a marketplace page */
document.addEventListener("click", async (e) => {
  const s = e.target.closest("[data-start-job]");
  if (s) return startJobDialog(s.dataset.startJob, s.dataset.id, s.dataset.name);
  const h = e.target.closest("[data-hire]");
  if (!h) return;
  h.disabled = true;
  try {
    const r = await hire(h.dataset.hire, h.dataset.pk, h.dataset.name);
    window.dispatchEvent(new CustomEvent("market:hired"));
    startJobDialog(h.dataset.hire, r.id, h.dataset.name);
  } catch (err) { toast(err.message, "err"); h.disabled = false; }
});

/* The "Marketplace" card on an agent's or team's own page (/agents/<id>, /teams/<id>): publish, withdraw, see the profile. */
window.marketCard = (kind, m) => {
  if (!m) return "";
  const profile = m.pk ? `<a class="btn btn-ghost btn-sm" href="/hire/${kind === "team" ? "teams" : "agents"}/${m.pk}">${icon("eye", { size: 14 })}View profile</a>` : "";
  const cats = kind === "team" ? ["build", "improve", "add"] : ["planning", "writing", "design", "code", "growth", "quality"];
  const picker = `<select class="input" id="mk-cat" aria-label="Category">${cats.map(c => `<option value="${c}" ${m.category === c ? "selected" : ""}>${esc(CATEGORY[c])}</option>`).join("")}</select>`;
  let body;
  const team = kind === "team";
  if (m.listing === "official") body = `<p class="empty-note">${team ? "Shared with everyone, so it's in the marketplace as an official team." : "Shared with everyone, so it's in the marketplace as an official agent."}</p><div class="m-acts">${profile}</div>`;
  else if (m.copy) body = `<p class="empty-note">${team ? "You hired this team from the marketplace." : "You hired this agent from the marketplace. Its ratings count for the original."}</p><div class="m-acts">${profile}</div>`;
  else if (m.listing === "pending") body = `<p class="empty-note"><span class="badge pending">Waiting for review</span> <span>An admin checks it before it shows in the marketplace.</span></p>
    <div class="m-acts">${profile}<button class="btn btn-ghost btn-sm" type="button" data-mk="withdraw">Withdraw</button></div>`;
  else if (m.listing === "listed") body = `<p class="empty-note"><span class="badge listed">Listed</span> <span>Members can see it, hire it and rate its work. If you edit it, an admin reviews it again.</span></p>
    <div class="m-acts">${profile}<button class="btn btn-ghost btn-sm" type="button" data-mk="withdraw">Withdraw from the marketplace</button></div>`;
  else body = `${m.listing === "rejected" ? `<p class="empty-note" style="color:var(--danger)"><span>Not approved.</span> ${m.note ? `<span data-no-i18n>${esc(m.note)}</span> ` : ""}<span>Change it and publish again.</span></p>` : ""}
    <p class="empty-note">${team ? "Publish it so other members can hire it. They'll see its agents, their instructions and its starting prompt, and an admin checks it first." : "Publish it so other members can hire it. They'll see its instructions, and an admin checks it first."}</p>
    <div class="m-acts">${picker}<button class="btn btn-dark btn-sm" type="button" data-mk="publish">${icon("briefcase", { size: 14 })}Publish to the marketplace</button></div>`;
  return `<section class="card market-card"><h2>${icon("briefcase", { size: 16 })}Marketplace</h2>${body}</section>`;
};
window.bindMarketCard = (root, kind, id, reload) => root.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-mk]"); if (!b) return;
  b.disabled = true;
  try {
    if (b.dataset.mk === "publish") {
      const r = await api("/api/market/publish", { method: "POST", body: JSON.stringify({ kind, id, category: root.querySelector("#mk-cat").value }) });
      toast(r.listing === "listed" ? "Listed in the marketplace" : "Sent for review");
    } else {
      await api("/api/market/withdraw", { method: "POST", body: JSON.stringify({ kind, id }) });
      toast("Withdrawn from the marketplace");
    }
    reload();
  } catch (err) { toast(err.message, "err"); b.disabled = false; }
});
