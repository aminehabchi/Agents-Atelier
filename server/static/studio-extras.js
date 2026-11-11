/* Studio extras, loaded after the studio's own script (studio.html):

   1. The cost estimate: what a run will probably cost on the owner's Claude account and how long it takes
      (GET /api/projects/<id>/estimate, see main.py run_estimate). Shown next to the composer for the team that's on,
      and on the Orchestrator's plan card for the agents it picked.
   2. The studio tour: a short first-run walk through the studio for new members (five steps, remembered once done).

   It uses the studio's globals (PID, selected, busy, canEdit, renderTeam, showPlan, showTab, registerCommands). */
(function () {
  if (typeof PID === "undefined") return;

  /* ---------------------------------------------------------------- cost estimate */
  const money2 = (n) => n >= 10 ? `$${n.toFixed(0)}` : `$${n.toFixed(2)}`;
  const minutes = (s) => s < 90 ? "about a minute" : `about ${Math.round(s / 60)} min`;
  let estTimer = null, estKey = "";

  function estimateText(e) {
    return `About ${money2(e.cost)} · ${minutes(e.seconds)}`;
  }
  function estimateTitle(e) {
    const lines = e.agents.map(a => `${a.name}: ${money2(a.cost)}`);
    const basis = e.measured === e.count ? "From past runs." : e.measured ? "From past runs where there are some, else a typical figure." : "Typical figures: no runs to learn from yet.";
    return [`Likely between ${money2(e.low)} and ${money2(e.high)} on your Claude account.`, ...lines, basis,
            e.first_build ? "" : "The Orchestrator usually runs fewer agents on a follow-up, so it often costs less."].filter(Boolean).join("\n");
  }
  async function fetchEstimate(ids) {
    const q = ids && ids.length ? `?agents=${encodeURIComponent(ids.join(","))}` : "";
    return api(`/api/projects/${PID}/estimate${q}`);
  }
  async function refreshEstimate() {
    const hint = document.getElementById("teamHint");
    if (!hint || typeof canEdit !== "undefined" && !canEdit) return;
    const ids = [...(typeof selected !== "undefined" ? selected : [])].sort();
    const key = ids.join(",") + "|" + (typeof project !== "undefined" && project ? project.history?.length ?? "" : "");
    if (key === estKey) return;
    estKey = key;
    let e;
    try { e = await fetchEstimate(ids); } catch { return; }          // the estimate is a hint: no error to show
    let el = document.getElementById("estimate");
    if (!el) {
      el = document.createElement("span");
      el.id = "estimate";
      el.className = "estimate";
      el.setAttribute("role", "note");
      hint.after(el);
    }
    el.innerHTML = `${icon("zap", { size: 13 })}<span>${estimateText(e)}</span>`;
    el.title = estimateTitle(e);
    el.classList.toggle("over", !!e.over_limit);
    if (e.over_limit) el.title = `Above your $${e.limit} spending limit per run: the run will pause there and you can Continue.\n` + el.title;
  }
  const soon = () => { clearTimeout(estTimer); estTimer = setTimeout(refreshEstimate, 400); };

  if (typeof renderTeam === "function") {
    const orig = renderTeam;
    renderTeam = function () { const out = orig.apply(this, arguments); soon(); return out; };
  }
  if (typeof showPlan === "function") {
    const origPlan = showPlan;
    showPlan = function (ev) {
      const out = origPlan.apply(this, arguments);
      if (!replaying && ev && (ev.agents || []).length && !ev.first) {
        const card = [...document.querySelectorAll(".msg.orch")].pop();
        fetchEstimate(ev.agents).then(e => {
          if (!card || card.querySelector(".est-note")) return;
          card.insertAdjacentHTML("beforeend", `<p class="note est-note" title="${esc(estimateTitle(e))}">${icon("zap", { size: 14 })}<span>${estimateText(e)} for these agents</span></p>`);
        }).catch(() => {});
      }
      return out;
    };
  }
  addEventListener("load", soon);
  setTimeout(soon, 1500);

  /* ---------------------------------------------------------------- the studio tour */
  const TOUR_KEY = "atelier-studio-tour";
  const STEPS = [
    { sel: "#composer", title: "Describe what you want", text: "Write your request here, like a job post: the business, who it's for, the pages. Attach a sketch or photos if you have them. Enter sends it." },
    { sel: "#team", title: "Your team of agents", text: "Each tile is a specialist. They work one step at a time, some side by side, and you see who's working now. Add or remove agents for this project here." },
    { sel: "#estimate", fallback: "#teamHint", title: "What it will cost", text: "The agents run on your own Claude account. This shows about how much the next run costs and how long it takes, from past runs." },
    { sel: "#tabs", title: "Watch and check the site", text: "Preview the site at desktop, tablet or phone width, read or edit the code, and go back to any saved version." },
    { sel: '#tabs [data-tab="share"]', title: "Share it with your client", text: "Send a link that works without an account. Your client pins comments on the page, and you turn them into requests for the team." },
  ];
  let step = 0, layer = null;

  function place() {
    if (!layer) return;
    const s = STEPS[step];
    const target = document.querySelector(s.sel) || (s.fallback && document.querySelector(s.fallback));
    const ring = layer.querySelector(".tour-ring"), box = layer.querySelector(".tour-box");
    box.querySelector("h3").textContent = t(s.title);
    box.querySelector("p").textContent = t(s.text);
    box.querySelector(".tour-step").textContent = `${step + 1} / ${STEPS.length}`;
    box.querySelector("[data-tour-back]").hidden = step === 0;
    box.querySelector("[data-tour-next]").textContent = t(step === STEPS.length - 1 ? "Start building" : "Next");
    if (!target || !target.getClientRects().length) { ring.hidden = true; box.style.cssText = "inset-inline-start:50%;top:50%;transform:translate(-50%,-50%)"; return; }
    ring.hidden = false;
    const r = target.getBoundingClientRect(), pad = 6;
    Object.assign(ring.style, { left: `${r.left - pad}px`, top: `${r.top - pad}px`, width: `${r.width + pad * 2}px`, height: `${r.height + pad * 2}px` });
    const below = r.bottom + 200 < innerHeight;
    const left = Math.min(Math.max(12, r.left), innerWidth - 352);
    box.style.cssText = `left:${left}px;${below ? `top:${r.bottom + 14}px` : `top:${Math.max(12, r.top - 14 - box.offsetHeight)}px`}`;
  }
  function finish() {
    try { localStorage.setItem(TOUR_KEY, "done"); } catch {}
    layer?.remove(); layer = null;
    removeEventListener("resize", place);
    document.getElementById("input")?.focus();
  }
  window.startStudioTour = () => {
    if (layer) return;
    step = 0;
    layer = document.createElement("div");
    layer.className = "tour";
    layer.innerHTML = `<div class="tour-ring" aria-hidden="true"></div>
      <div class="tour-box" role="dialog" aria-modal="true" aria-labelledby="tour-title">
        <span class="tour-step" data-no-i18n></span><h3 id="tour-title"></h3><p></p>
        <div class="tour-acts"><button type="button" class="btn btn-ghost btn-sm" data-tour-skip>Skip the tour</button><span class="spacer"></span>
          <button type="button" class="btn btn-ghost btn-sm" data-tour-back>Back</button>
          <button type="button" class="btn btn-dark btn-sm" data-tour-next>Next</button></div></div>`;
    document.body.append(layer);
    layer.addEventListener("click", (e) => {
      if (e.target.closest("[data-tour-skip]")) return finish();
      if (e.target.closest("[data-tour-back]")) { step = Math.max(0, step - 1); return place(); }
      if (e.target.closest("[data-tour-next]")) { if (step === STEPS.length - 1) return finish(); step++; place(); }
    });
    layer.addEventListener("keydown", (e) => {
      if (e.key === "Escape") finish();
      if (e.key === "ArrowRight" && step < STEPS.length - 1) { step++; place(); }
      if (e.key === "ArrowLeft" && step > 0) { step--; place(); }
    });
    addEventListener("resize", place);
    place();
    layer.querySelector("[data-tour-next]").focus();
  };
  const seen = () => { try { return localStorage.getItem(TOUR_KEY) === "done"; } catch { return true; } };
  // first visit to a studio, on a screen wide enough to point at things, for someone who can work in it
  setTimeout(() => {
    if (!seen() && matchMedia("(min-width: 901px)").matches && (typeof canEdit === "undefined" || canEdit) && !document.querySelector("dialog[open]")) startStudioTour();
  }, 1800);
  registerCommands([{ group: "Help", icon: "help", title: "Show the studio tour", run: () => startStudioTour() }]);

  /* ---------------------------------------------------------------- styles */
  const css = document.createElement("style");
  css.textContent = `
  #composer .row:has(#estimate) .hint { display: none; }
  .estimate { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--stone); white-space: nowrap; cursor: help; }
  .estimate.over { color: var(--danger); }
  .estimate svg { flex: none; }
  .note.est-note { margin: 8px 0 0; display: flex; gap: 6px; align-items: center; cursor: help; }
  .tour { position: fixed; inset: 0; z-index: 1000; }
  .tour-ring { position: fixed; border-radius: var(--r-card); box-shadow: 0 0 0 9999px rgb(0 0 0 / .55), 0 0 0 2px var(--sky); transition: all .25s ease; pointer-events: none; }
  .tour-box { position: fixed; width: min(340px, calc(100vw - 24px)); background: var(--paper); color: var(--ink); border-radius: var(--r-card);
    box-shadow: var(--ring), 0 18px 50px rgb(0 0 0 / .35); padding: 18px; display: grid; gap: 8px; transition: top .25s ease, left .25s ease; }
  .tour-box h3 { margin: 0; font-size: 16px; font-weight: 500; }
  .tour-box p { margin: 0; font-size: 14px; line-height: 1.55; color: var(--graphite); }
  .tour-step { font-size: 12px; color: var(--sky); font-weight: 600; }
  .tour-acts { display: flex; gap: 6px; align-items: center; margin-top: 6px; }
  .tour-acts .spacer { flex: 1; }
  @media (prefers-reduced-motion: reduce) { .tour-ring, .tour-box { transition: none; } }`;
  document.head.append(css);
})();
