/* Agents Atelier icon set: thin-stroke line icons on a 24px grid, hand-drawn for this project.
 *
 *   icon("menu")                -> "<svg ...>" string
 *   icon("menu", { size: 16 })
 *   <i data-icon="menu"></i>    -> filled in automatically (also for elements added later)
 */
(function () {
  const P = (d) => `<path d="${d}"/>`;
  const R = (x, y, w, h, r = 2) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}"/>`;
  const C = (cx, cy, r) => `<circle cx="${cx}" cy="${cy}" r="${r}"/>`;

  const ICONS = {
    // ---------- interface ----------
    logo: `<path d="M7 12h2.4M14.6 12h2.4" opacity=".6"/><circle cx="4.8" cy="12" r="2" fill="currentColor" stroke="none" opacity=".6"/>`
      + `<circle cx="12" cy="12" r="2" fill="currentColor" stroke="none" opacity=".8"/><circle cx="19.2" cy="12" r="2.5" fill="currentColor" stroke="none"/>`,
    home: P("M4 11l8-7 8 7v8a1 1 0 0 1-1 1h-4v-6h-6v6H5a1 1 0 0 1-1-1z"),
    menu: P("M4 6h16M4 12h16M4 18h16"),
    plus: P("M12 5v14M5 12h14"),
    x: P("M6 6l12 12M18 6L6 18"),
    user: C(12, 8, 4) + P("M4 21c0-4 4-6 8-6s8 2 8 6"),
    "log-out": P("M9 4H5v16h4M16 8l4 4-4 4M20 12H10"),
    check: P("M5 12.5l4.5 4.5L19 7"),
    "arrow-right": P("M5 12h14M13 6l6 6-6 6"),
    "arrow-up": P("M12 19V5M6 11l6-6 6 6"),
    stop: R(6, 6, 12, 12, 2),
    download: P("M12 4v11M7 10l5 5 5-5M5 20h14"),
    refresh: P("M20 12a8 8 0 1 1-2.34-5.66M20 4v5h-5"),
    external: P("M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"),
    monitor: R(3, 4, 18, 12) + P("M8 20h8M12 16v4"),
    tablet: R(5, 3, 14, 18) + P("M11 18h2"),
    phone: R(7, 3, 10, 18) + P("M11 18h2"),
    trash: P("M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"),
    copy: R(8, 8, 12, 12) + P("M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"),
    search: C(11, 11, 6) + P("M20 20l-4.5-4.5"),
    grid: R(4, 4, 7, 7, 1.5) + R(13, 4, 7, 7, 1.5) + R(4, 13, 7, 7, 1.5) + R(13, 13, 7, 7, 1.5),
    folder: P("M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"),
    clock: C(12, 12, 8) + P("M12 8v4l3 2"),
    file: P("M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5"),
    "file-plus": P("M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M12 11v6M9 14h6"),
    pencil: P("M4 20l1-4L16 5l3 3L8 19zM14 7l3 3"),
    eye: P("M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z") + C(12, 12, 3),
    code: P("M8 7l-5 5 5 5M16 7l5 5-5 5"),
    message: P("M4 5h16v11H9l-5 4z"),
    alert: C(12, 12, 9) + P("M12 8v5M12 16h.01"),
    layers: P("M12 3l9 5-9 5-9-5zM3 13l9 5 9-5"),
    users: C(9, 8, 3.5) + P("M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6 6 0 0 1 3.5 6"),
    play: P("M7 4.5v15l12-7.5z"),
    pause: P("M9 5v14M15 5v14"),
    sun: C(12, 12, 4) + P("M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4"),
    moon: P("M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"),
    history: P("M3.5 12a8.5 8.5 0 1 0 2.6-6.1M3 4.5V9h4.5M12 7.5V12l3 2"),
    chart: P("M4 20h16M7 16v-5M12 16V6M17 16v-8"),
    paperclip: P("M20 11.5l-8.2 8.2a5 5 0 0 1-7.1-7.1l8.2-8.2a3.3 3.3 0 0 1 4.7 4.7l-8.2 8.2a1.7 1.7 0 0 1-2.4-2.4l7.6-7.6"),
    save: P("M5 4h11l3 3v13H5zM8 4v5h7V4M8 20v-6h8v6"),
    compare: P("M8 4v12M8 16l-3-3M8 16l3-3M16 20V8M16 8l-3 3M16 8l3 3"),
    sliders: P("M4 7h10M18 7h2M4 17h4M12 17h8") + C(16, 7, 2) + C(10, 17, 2),
    cpu: R(6, 6, 12, 12, 2) + P("M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"),
    "chevron-down": P("M6 9l6 6 6-6"),
    "chevron-up": P("M6 15l6-6 6 6"),
    lock: R(5, 11, 14, 10) + P("M8 11V7a4 4 0 0 1 8 0v4"),
    help: C(12, 12, 9) + P("M9.6 9.3a2.5 2.5 0 0 1 4.9.7c0 1.7-2.5 2.2-2.5 3.8M12 17h.01"),
    book: P("M4 19.5V5a2 2 0 0 1 2-2h14v15H6.5A2.5 2.5 0 0 0 4 20.5 2.5 2.5 0 0 0 6.5 23H20M8 7h8M8 11h5"),
    keyboard: R(2.5, 6, 19, 12) + P("M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"),
    lightbulb: P("M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"),

    // ---------- agents ----------
    clipboard: R(6, 4, 12, 17) + R(9, 2.5, 6, 3, 1) + P("M9 11h6M9 15h4"),
    compass: C(12, 12, 9) + P("M15.5 8.5l-2 5-5 2 2-5z"),
    palette: P("M12 3a9 9 0 1 0 0 18c1.1 0 1.5-.8 1.5-1.5 0-1.2-1-1.5-1-2.5s.8-1.5 1.8-1.5H17a4 4 0 0 0 4-4c0-4.7-4-8.5-9-8.5z")
      + C(7.5, 11.5, 1) + C(9.5, 7.5, 1) + C(14, 7, 1),
    type: P("M5 7V5h14v2M12 5v14M9 19h6"),
    image: R(3, 4, 18, 16) + C(8.5, 9.5, 1.5) + P("M21 16l-5-5-9 9"),
    brush: P("M19 3l2 2-9.5 9.5-2-2zM9.5 12.5C7 12.5 5 14.1 5 16.7c0 1.3-.7 2.3-2 3.3 3.5.8 8 .2 8.5-4.5"),
    devices: R(2, 5, 13, 10, 1.5) + R(17, 8, 5, 11, 1) + P("M5 19h7"),
    zap: P("M13 3L5 13h6l-1 8 8-10h-6z"),
    sparkles: P("M11 3l1.8 5.2L18 10l-5.2 1.8L11 17l-1.8-5.2L4 10l5.2-1.8zM19 15l.7 1.8 1.8.7-1.8.7L19 20l-.7-1.8-1.8-.7 1.8-.7z"),
    accessibility: C(12, 4.5, 1.5) + P("M5 8l7 1.5L19 8M12 9.5V14M12 14l-3 7M12 14l3 7"),
    gauge: P("M5.6 18.4a9 9 0 1 1 12.8 0M12 13.5l3.5-3.5") + C(12, 13.5, 1.2),
    bug: R(7, 8, 10, 12, 5) + P("M12 12v8M9 5l1.5 3M15 5l-1.5 3M3.5 13h3.5M17 13h3.5M5 19.5l2.5-1.5M19 19.5L16.5 18M5 8.5l2.5 2M19 8.5l-2.5 2"),
    award: C(12, 9, 6) + P("M9 14.5L8 21l4-2 4 2-1-6.5"),
    bot: R(5, 8, 14, 11, 3) + P("M12 4v4M9.5 13h.01M14.5 13h.01"),
  };

  function icon(name, opts = {}) {
    const size = opts.size || 18;
    const body = ICONS[name] || ICONS.bot;
    return `<svg class="ic ${opts.cls || ""}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="${opts.stroke || 1.5}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
  }

  function hydrate(root) {
    root.querySelectorAll?.("i[data-icon]:not([data-done])").forEach((el) => {
      el.dataset.done = "1";
      el.innerHTML = icon(el.dataset.icon, { size: +el.dataset.size || undefined });
    });
  }

  window.ICONS = ICONS;
  window.icon = icon;
  const start = () => {
    hydrate(document);
    new MutationObserver((muts) => muts.forEach((m) => m.addedNodes.forEach((n) => n.nodeType === 1 && hydrate(n.parentNode || n))))
      .observe(document.body, { childList: true, subtree: true });
  };
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", start) : start();
})();
