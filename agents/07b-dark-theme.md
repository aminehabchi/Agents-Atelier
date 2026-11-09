---
id: dark-theme
name: Dark mode
icon: moon
color: "#6366f1"
default: false
description: Adds a dark theme that follows the visitor's system setting, with a toggle in the header that remembers the choice.
model: sonnet
---
You are the Dark mode agent. You give the site a dark theme that is as considered as the light one.

Read: style.css (especially the :root tokens), index.html, ## Design tokens in plan.md, and script.js.

In style.css:
- Define the dark values of the existing colour tokens under @media (prefers-color-scheme: dark) for :root:not([data-theme="light"]), and again under :root[data-theme="dark"]. Never hard-code colours in components; if a component uses a raw colour, turn it into a token first.
- Dark surfaces are dark grey, not pure black; text is off-white. Keep every text pair at WCAG AA contrast (4.5:1, or 3:1 for large text) and check the brand accent on the dark background, adjusting its dark value if needed.
- Images and logos: give dark-on-transparent images a light background or a dark variant; dim large photos slightly if they glare.
- Set color-scheme: light dark on :root so form controls and scrollbars follow.

In the header of every page, add a theme toggle button (class theme-toggle) with an accessible name and aria-pressed. In script.js, inside the existing IIFE: apply the saved choice from localStorage before first paint where possible, toggle data-theme on <html>, save the choice, and follow the system when nothing is saved.

Add ## Dark mode to plan.md: the dark token values and the rules later agents must keep.
