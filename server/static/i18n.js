/* The interface language. Pages are written in English; in French this translates the page as it renders.

   The server only ever stores the choice. Translations live in i18n-fr.js, keyed by the exact English text:
   - a piece of text, an attribute (title, placeholder, aria-label, alt), or an element with simple inline markup
     such as <p>Click <b>New</b></p> (its normalised innerHTML is the key)
   - I18N_FR_PATTERNS for text with numbers or names in it

   What people wrote is never translated: put data-no-i18n on it, or add its container to SKIP below. */
(function () {
  const SUPPORTED = ["en", "fr"];
  const stored = () => { try { return localStorage.getItem("atelier-lang"); } catch { return null; } };
  const browser = () => ((navigator.language || "en").toLowerCase().startsWith("fr") ? "fr" : "en");
  const lang = SUPPORTED.includes(stored()) ? stored() : browser();

  window.getLang = () => lang;
  /* Remember a language and show it. `reload` is off when the page is about to move anyway. */
  window.setLang = (next, reload = true) => {
    if (!SUPPORTED.includes(next)) return;
    try { localStorage.setItem("atelier-lang", next); } catch {}
    if (reload && next !== lang) location.reload();
  };
  document.documentElement.lang = lang;
  window.t = (text) => text;                // for text that isn't on the page, such as a confirm() message
  if (lang !== "fr") return;

  document.write('<script src="/static/i18n-fr.js"><\/script>');

  const SKIP = "script, style, pre, iframe, svg, [data-no-i18n], .msg.user, .msg .text";
  const RICH_TAGS = new Set(["B", "STRONG", "EM", "CODE", "KBD", "A", "BR", "SPAN"]);
  const ATTRS = ["title", "placeholder", "aria-label", "alt"];
  const debug = (() => { try { return !!localStorage.getItem("atelier-i18n-debug"); } catch { return false; } })();
  const misses = window.__i18nMisses = new Set();
  const norm = (s) => s.replace(/\s+/g, " ").trim();
  const hasLetters = (s) => /\p{L}{2,}/u.test(s);
  const dict = () => window.I18N_FR || {};

  function translate(key) {
    if (Object.prototype.hasOwnProperty.call(dict(), key)) return dict()[key];
    for (const [re, to] of window.I18N_FR_PATTERNS || []) {
      if (re.test(key)) {
        return key.replace(re, (...m) => typeof to === "function" ? to(...m) : to.replace(/\$(\d)/g, (_, n) => translateInner(m[+n])));
      }
    }
    return null;
  }
  /* A captured piece of a pattern (like "Research") may have its own translation. */
  const translateInner = (s) => (s && Object.prototype.hasOwnProperty.call(dict(), s) ? dict()[s] : s ?? "");
  window.t = (text) => translate(norm(String(text))) ?? text;

  function translateText(node) {
    const raw = node.nodeValue;
    if (!raw || !hasLetters(raw)) return;
    const key = norm(raw);
    if (isFrenchAlready.has(key)) return;    // our own French coming back through the observer
    const fr = translate(key);
    if (fr === null) { if (debug) misses.add(key); return; }
    if (fr === key) return;
    isFrenchAlready.add(norm(fr));
    const start = raw.match(/^\s*/)[0], end = raw.match(/\s*$/)[0];
    node.nodeValue = start + fr + end;
  }
  const isFrenchAlready = new Set();

  function translateAttrs(el) {
    for (const a of ATTRS) {
      const raw = el.getAttribute(a);
      if (!raw || !hasLetters(raw)) continue;
      const key = norm(raw);
      if (isFrenchAlready.has(key)) continue;
      const fr = translate(key);
      if (fr === null) { if (debug) misses.add(`[${a}] ${key}`); continue; }
      if (fr !== key) { isFrenchAlready.add(norm(fr)); el.setAttribute(a, fr); }
    }
  }

  /* An element holding only text and simple inline markup, as one sentence. */
  function isRich(el) {
    if (!el.firstElementChild || !el.textContent.trim()) return false;
    return [...el.children].every(c => RICH_TAGS.has(c.tagName) && [...c.children].every(g => RICH_TAGS.has(g.tagName)));
  }

  function walk(el) {
    if (el.nodeType === Node.TEXT_NODE) {
      if (!el.parentElement?.closest(SKIP)) translateText(el);
      return;
    }
    if (el.nodeType !== Node.ELEMENT_NODE || el.closest(SKIP)) return;
    translateAttrs(el);
    if (el.tagName === "TEXTAREA") return;       // its text is what someone typed
    if (isRich(el)) {
      const key = norm(el.innerHTML);
      if (isFrenchAlready.has(key)) return;
      const fr = translate(key);             // our own text, on an exact match of markup already on the page
      if (fr !== null) {
        if (fr !== key) { el.innerHTML = fr; isFrenchAlready.add(norm(el.innerHTML)); }
        return;
      }
      if (debug && !isFrenchAlready.has(key) && !el.closest("a") && !/^(B|STRONG|EM|CODE|KBD|A)$/.test(el.tagName)) misses.add(`<html> ${key}`);
    }
    for (const child of [...el.childNodes]) walk(child);
  }

  const observer = new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === "characterData") { if (!r.target.parentElement?.closest(SKIP)) translateText(r.target); }
      else if (r.type === "attributes") { if (!r.target.closest(SKIP)) translateAttrs(r.target); }
      else for (const n of r.addedNodes) walk(n);
    }
  });
  observer.observe(document, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  document.addEventListener("DOMContentLoaded", () => walk(document.documentElement));
})();
