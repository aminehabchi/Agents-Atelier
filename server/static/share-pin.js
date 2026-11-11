/* Runs inside a shared site (added by the server to every page served through a share link, see share.py).
   It never changes the site itself: it draws the comment pins on a layer above the page and, in comment mode,
   tells the share page (its parent window) which spot the visitor clicked.

   Messages, all marked { atelier: 1 }:
     parent -> page   { type: "mode", on }         comment mode on or off
                      { type: "pins", pins }       the pins of this page: [{ id, n, selector, x, y, doc_x, doc_y }]
                      { type: "focus", id }        scroll to a pin and make it pulse
     page -> parent   { type: "ready", page }      this page loaded (its path, e.g. /s/<token>/site/about.html)
                      { type: "spot", ... }        where the visitor clicked: selector, x, y (0..1 in that element),
                                                   doc_x (0..1 of the page width), doc_y (px), viewport, snippet
                      { type: "open", id }         the visitor clicked a pin
                      { type: "escape" }           Esc pressed in comment mode */
(function () {
  if (window.parent === window) return;
  const BLUE = "#4f5bd5";
  let mode = false, pins = [], layer = null, hoverBox = null, timer = null;
  const post = (msg) => window.parent.postMessage(Object.assign({ atelier: 1 }, msg), "*");

  function selectorOf(el) {
    const parts = [];
    while (el && el.nodeType === 1 && el !== document.documentElement) {
      if (el.id && /^[A-Za-z][\w-]*$/.test(el.id) && document.querySelectorAll("#" + el.id).length === 1) {
        parts.unshift("#" + el.id);
        break;
      }
      let part = el.tagName.toLowerCase();
      const parent = el.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter(c => c.tagName === el.tagName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(el) + 1})`;
      }
      parts.unshift(part);
      el = parent;
    }
    return parts.join(" > ").slice(0, 400);
  }
  const docWidth = () => Math.max(document.documentElement.scrollWidth, document.documentElement.clientWidth, 1);

  function ensureLayer() {
    if (layer && layer.isConnected) return layer;
    layer = document.createElement("div");
    layer.setAttribute("data-atelier-layer", "");
    layer.style.cssText = "position:absolute;top:0;left:0;width:0;height:0;z-index:2147483646;pointer-events:none";
    document.documentElement.appendChild(layer);
    return layer;
  }
  function spotOf(p) {
    let el = null;
    try { el = p.selector ? document.querySelector(p.selector) : null; } catch { el = null; }
    if (el) {
      const r = el.getBoundingClientRect();
      if (r.width || r.height) return { left: r.left + scrollX + p.x * r.width, top: r.top + scrollY + p.y * r.height };
    }
    return { left: p.doc_x * docWidth(), top: p.doc_y };
  }
  function place() {
    if (!layer) return;
    layer.querySelectorAll("[data-pin]").forEach(m => {
      const p = pins.find(x => x.id === m.dataset.pin);
      if (!p) return;
      const s = spotOf(p);
      m.style.left = s.left + "px";
      m.style.top = s.top + "px";
    });
  }
  function render() {
    ensureLayer().innerHTML = "";
    pins.forEach(p => {
      const m = document.createElement("button");
      m.type = "button";
      m.dataset.pin = p.id;
      m.textContent = p.n;
      m.title = "Comment " + p.n;
      m.style.cssText = `position:absolute;transform:translate(-50%,-50%);width:28px;height:28px;border-radius:50% 50% 50% 4px;
        border:2px solid #fff;background:${BLUE};color:#fff;font:600 12px/24px system-ui,sans-serif;text-align:center;padding:0;
        cursor:pointer;pointer-events:auto;box-shadow:0 2px 8px rgba(0,0,0,.35);transition:transform .2s`;
      m.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); post({ type: "open", id: p.id }); });
      layer.appendChild(m);
    });
    place();
    clearInterval(timer);
    if (pins.length) timer = setInterval(place, 1000);   // pages move: images load, sections animate in
  }
  function highlight(el) {
    if (!hoverBox) {
      hoverBox = document.createElement("div");
      hoverBox.style.cssText = `position:absolute;pointer-events:none;border:2px dashed ${BLUE};border-radius:4px;background:rgba(79,91,213,.08);transition:all .08s`;
      ensureLayer().appendChild(hoverBox);
    }
    if (!el) { hoverBox.style.display = "none"; return; }
    const r = el.getBoundingClientRect();
    Object.assign(hoverBox.style, { display: "block", left: r.left + scrollX + "px", top: r.top + scrollY + "px", width: r.width + "px", height: r.height + "px" });
  }
  const isOurs = (el) => !!(el && el.closest && el.closest("[data-atelier-layer]"));

  document.addEventListener("mouseover", (e) => { if (mode && !isOurs(e.target)) highlight(e.target); }, true);
  document.addEventListener("click", (e) => {
    if (!mode || isOurs(e.target)) return;
    e.preventDefault();
    e.stopPropagation();
    const el = e.target.nodeType === 1 ? e.target : e.target.parentElement;
    const r = el.getBoundingClientRect();
    post({
      type: "spot", selector: selectorOf(el),
      x: r.width ? (e.clientX - r.left) / r.width : 0, y: r.height ? (e.clientY - r.top) / r.height : 0,
      doc_x: e.pageX / docWidth(), doc_y: e.pageY, viewport: innerWidth,
      snippet: (el.innerText || el.getAttribute("alt") || el.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim().slice(0, 120),
    });
  }, true);
  document.addEventListener("keydown", (e) => { if (mode && e.key === "Escape") post({ type: "escape" }); });
  addEventListener("resize", place);
  addEventListener("message", (e) => {
    if (e.source !== window.parent) return;
    const d = e.data || {};
    if (!d.atelier) return;
    if (d.type === "mode") {
      mode = !!d.on;
      document.documentElement.style.cursor = mode ? "crosshair" : "";
      if (!mode) highlight(null);
    } else if (d.type === "pins") {
      pins = Array.isArray(d.pins) ? d.pins : [];
      render();
    } else if (d.type === "focus") {
      const m = layer && layer.querySelector(`[data-pin="${CSS.escape(String(d.id))}"]`);
      const p = pins.find(x => x.id === d.id);
      if (!p) return;
      const s = spotOf(p);
      scrollTo({ top: Math.max(0, s.top - innerHeight / 3), behavior: "smooth" });
      if (m) { m.style.transform = "translate(-50%,-50%) scale(1.5)"; setTimeout(() => { m.style.transform = "translate(-50%,-50%)"; }, 700); }
    }
  });
  const ready = () => post({ type: "ready", page: location.pathname });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready); else ready();
})();
