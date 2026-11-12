"""Contact details some agents can't work without (a WhatsApp number, an email address…).

Before a run, the server checks which of these the chosen agents need and which the project doesn't have yet. Missing ones
are asked in the chat with the Orchestrator's question form, before any agent starts (first builds included). Answers are
checked, saved in the project ("contact") and given to every agent in <project_brief>. A detail the user skips is
remembered ("contact_skipped") so it isn't asked again; the agent then leaves its button hidden.
"""

import re

# key -> (question, placeholder, label shown to agents)
FIELDS = {
    "whatsapp": ("Your WhatsApp number (with the country code)", "+212 6 12 34 56 78", "WhatsApp number"),
    "phone": ("The phone number visitors should call", "+212 5 22 00 00 00", "Phone number"),
    "email": ("The email address for contact messages", "contact@example.com", "Email address"),
    "telegram": ("Your Telegram username", "@your_username", "Telegram username"),
}
# agent id -> the details it needs
NEEDS = {"whatsapp": ["whatsapp"], "email": ["email"], "call": ["phone"], "telegram": ["telegram"]}

EMAIL_RE = re.compile(r"^[^\s@<>\"']{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
TELEGRAM_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


def missing(agent_ids: list[str], meta: dict) -> list[str]:
    """The details the chosen agents need that the project has neither saved nor been told to skip."""
    have = {k for k, v in (meta.get("contact") or {}).items() if v}
    skipped = set(meta.get("contact_skipped") or [])
    keys = []
    for a in agent_ids:
        for k in NEEDS.get(a, []):
            if k not in have and k not in skipped and k not in keys:
                keys.append(k)
    return keys


def questions(keys: list[str]) -> list[dict]:
    return [{"id": f"q{i + 1}", "text": FIELDS[k][0], "kind": "text", "options": [], "other": False, "placeholder": FIELDS[k][1]}
            for i, k in enumerate(keys)]


def clean(key: str, value: str) -> str:
    """The detail in a standard form, or ValueError with a message for the user."""
    value = " ".join(str(value or "").split())
    if key in ("whatsapp", "phone"):
        digits = re.sub(r"[\s().-]", "", value)
        digits = "+" + digits[2:] if digits.startswith("00") else digits
        if not re.fullmatch(r"\+?\d{8,15}", digits):
            raise ValueError(f"“{value[:60]}” doesn't look like a phone number. Write it with digits, e.g. {FIELDS[key][1]}.")
        return digits
    if key == "email":
        if not EMAIL_RE.match(value) or len(value) > 120:
            raise ValueError(f"“{value[:60]}” doesn't look like an email address, e.g. {FIELDS[key][1]}.")
        return value
    if key == "telegram":
        name = re.sub(r"^(https?://)?(t\.me/|telegram\.me/)|^@", "", value).strip("/")
        if not TELEGRAM_RE.match(name):
            raise ValueError(f"“{value[:60]}” isn't a Telegram username: 5–32 letters, digits or _, starting with a letter.")
        return name
    raise ValueError("Unknown contact detail.")


def apply(meta: dict, keys: list[str], qs: list[dict], given: dict) -> list[dict]:
    """Save the answers in meta (raises ValueError on a bad one, before anything is saved). Returns [{question, answer}]."""
    found, pairs = {}, []
    for k, q in zip(keys, qs):
        raw = given.get(q["id"])
        raw = raw[0] if isinstance(raw, list) and raw else raw
        found[k] = clean(k, raw) if str(raw or "").strip() else ""
        pairs.append({"question": q["text"], "answer": found[k] or "Not now: keep this button hidden"})
    contact = dict(meta.get("contact") or {})
    skipped = list(meta.get("contact_skipped") or [])
    for k, v in found.items():
        if v:
            contact[k] = v
            skipped = [s for s in skipped if s != k]
        elif k not in skipped:
            skipped.append(k)
    meta["contact"], meta["contact_skipped"] = contact, skipped
    return pairs


def block(meta: dict) -> str:
    """Lines for <project_brief>: the saved details, and the ones the owner chose not to give yet."""
    contact = meta.get("contact") or {}
    lines = [f"{FIELDS[k][2]} (confirmed by the owner): {v}" for k, v in contact.items() if k in FIELDS and v]
    skipped = [FIELDS[k][2] for k in meta.get("contact_skipped") or [] if k in FIELDS and not contact.get(k)]
    if skipped:
        lines.append("Not given yet (leave it empty in CONTACT and keep its button hidden): " + ", ".join(skipped))
    return "\n".join(lines)
