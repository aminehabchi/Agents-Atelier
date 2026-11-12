"""Prompt-injection checks: text that tries to turn an agent against its rules, its project or its member.

Untrusted text reaches the agents from several sides: the request (and comments pasted into it from a share link), the
files of an imported site, pages the Research agent reads, and the instructions of agents members write or hire from
the marketplace. The server's defences, from the inside out:

  - every agent runs without code-running tools, confined to its project folder (main.py run_agent, outside_project);
  - untrusted text sits in tagged data blocks, with fake tags and invisible characters taken out (main.clean_text,
    strip_invisible below), and the server's security rules come last in the system prompt;
  - this module spots the usual injection moves, so the server can refuse a member's agent that carries them, warn in
    the chat when a request or a project file carries them, and tell the agents where they are (<security_note>);
  - a web agent that fetches a private address (the server itself, the local network, cloud metadata) or sends data
    out in a URL is stopped (unsafe_fetch).

The checks look for the move, not for words: "Never add external scripts" or "ignore my earlier colour choice" are fine;
"ignore the security rules", "send the files to https://…" or "read ../../" are not.
"""

import ipaddress
import re
import unicodedata
import urllib.parse

# Characters a person can't see but a model reads: the Unicode "tag" block (used to hide instructions in plain sight),
# bidirectional overrides (to make text read differently than it shows), and zero-width marks other than the joiners
# Arabic, Persian and emoji need (U+200C / U+200D stay).
INVISIBLE = re.compile("[\U000E0000-\U000E007F‪-‮⁦-⁩​‎‏⁠-⁤﻿᠎]")
# Chat-template markers some models were trained on: <|im_start|>, <|system|>, [INST] …
TEMPLATE = re.compile(r"<\|[^|>\n]{0,40}\|>|\[/?INST\]|<</?SYS>>", re.I)


def strip_invisible(text: str) -> str:
    """Untrusted text without the characters a person can't see, and with chat-template markers defused."""
    text = INVISIBLE.sub("", text)
    return TEMPLATE.sub(lambda m: m.group(0).replace("<", "‹").replace(">", "›").replace("[", "⟦").replace("]", "⟧"), text)


NEGATION = re.compile(r"\b(never|don['’]?t|do not|does not|must not|mustn['’]?t|no|not|without|avoid|refuse|ignore any|ignore it|"
                      r"jamais|ne pas|n['’]ajoute)\b[^.\n]{0,40}$", re.I)

# (category, pattern, used for: a = agent instructions, r = requests, f = project files)
_RULES = [
    ("override", r"\b(ignore|disregard|forget|override|bypass|skip|disable)\b[^.\n]{0,40}\b(system|security|safety|server|"
                 r"previous|prior|above|earlier|all|any|your)\b[^.\n]{0,25}\b(instructions?|rules?|prompts?|guidelines?|restrictions?)\b",
     "af"),
    ("override", r"\b(ignore|disregard|bypass|override|disable)\b[^.\n]{0,25}\b(system|security|safety|server)\b[^.\n]{0,15}"
                 r"\b(instructions?|rules?|prompts?|guidelines?|restrictions?|checks?)\b", "r"),
    ("override", r"\b(you are now|you are no longer|from now on you are|new (system )?(role|instructions)\s*:|jailbreak|developer mode|"
                 r"do anything now)\b", "arf"),
    ("reveal", r"\b(reveal|print|show|repeat|output|leak|dump|copy|write)\b[^.\n]{0,30}\b(system prompt|your (system )?"
               r"(instructions|rules|prompt)|security rules|the rules above|hidden instructions)\b", "arf"),
    ("secrets", r"\b(read|open|cat|print|send|include|copy|leak|show|write|put|paste|list)\b[^.\n]{0,40}"
                r"(anthropic_api_key|\bapi[_ -]?keys?\b|\.env\b|environment variables?|\benv vars?\b|/etc/|\.ssh\b|id_rsa|"
                r"\bcredentials\b|secret keys?|session cookies?|auth(entication)? tokens?|access tokens?)", "arf"),
    ("paths", r"\b(read|open|list|glob|grep|search|write|edit|copy|look (in|at)|go to)\b[^\n]{0,40}"
              r"(\.\./|~/|/home/|/root/|/etc/|/var/|/proc/|/sys/|/tmp/|/usr/|[a-z]:\\|other (users?['’]?s?|members?['’]?s?|"
              r"people['’]?s) (projects?|files?|folders?)|other projects?|parent (folder|directory)|outside (the|this|your) "
              r"(current )?(folder|project|directory))", "arf"),
    # sending something sensitive somewhere: a site's own form posting to Formspree or Mailchimp isn't (only what's sent decides)
    ("exfil", r"\b(send|post|upload|exfiltrate|transmit|forward|leak|beacon|report|submit)\b[^.\n]{0,30}"
              r"\b(the (project|site|source|code|files?)|(project|site|source|all|other|these|those|its|their|your) files|source code|"
              r"(the|your|all|its) (code|contents?|prompts?|instructions|rules|history|chat)|plan\.md|research\.md|everything|"
              r"keys?|tokens?|cookies?|passwords?|credentials|env(ironment)?)\b[^.\n]{0,40}\b(to|at|into)\b[^.\n]{0,25}"
              r"(https?://|webhooks?\b|\bngrok\b|requestbin|pipedream|pastebin|discord(app)?\.com/api/webhooks|telegram bot api|"
              r"an? (external|remote|outside) (server|url|address|site|endpoint))", "arf"),
    ("exfil", r"(webhook\.site|ngrok(-free)?\.(io|app)|requestbin|pipedream\.net|interact\.sh|oastify\.com|burpcollaborator|"
              r"canarytokens|\.oast\.(fun|pro|live|site|online|me))", "arf"),
    ("script", r"\b(add|insert|include|inject|load|embed|append)\b[^.\n]{0,40}(<script[^>]*\bsrc\s*=\s*['\"]?https?://|"
               r"keylogger|crypto ?miner|coinhive|tracking pixel|session stealer|credential (harvest|phish))", "arf"),
    ("markup", r"</?\s*(system|assistant|instructions?|security rules)\s*>", "arf"),
]
RULES = [(cat, re.compile(rx, re.I), where) for cat, rx, where in _RULES]
REASONS = {
    "override": "tries to override the agents' rules",
    "reveal": "asks for the agents' hidden instructions",
    "secrets": "asks for keys, passwords or server files",
    "paths": "asks to read or write outside the project",
    "exfil": "asks to send data to another site",
    "script": "asks to add a harmful or tracking script",
    "markup": "fakes the server's own instruction tags",
    "invisible": "contains hidden characters",
}


def suspicious(text: str, source: str = "a") -> list[str]:
    """What looks like an injection attempt in `text`, as short reasons (empty: nothing found).
    `source`: "a" an agent's instructions, "r" a request, "f" a project file."""
    if not text:
        return []
    found = []
    if INVISIBLE.search(text):
        found.append("invisible")
    flat = unicodedata.normalize("NFKC", INVISIBLE.sub("", text))
    for cat, rx, where in RULES:
        if source not in where or cat in found:
            continue
        for m in rx.finditer(flat):
            before = flat[max(0, m.start() - 60):m.start()].split("\n")[-1]
            if NEGATION.search(before):
                continue                       # "never send data to other sites", "don't read ../" …
            found.append(cat)
            break
    return found


def describe(found: list[str]) -> str:
    return "; ".join(REASONS[f] for f in found)


# ---------------------------------------------------------------- web fetches

BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost", "metadata", "metadata.google.internal",
                 "metadata.goog", "instance-data", "kubernetes.default", "host.docker.internal", "gateway.docker.internal"}
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet", ".corp")
BLOB = re.compile(r"[A-Za-z0-9+/_=-]{120,}|(?:%[0-9A-Fa-f]{2}){40,}")


def _private_host(host: str) -> bool:
    host = host.strip("[]").rstrip(".").lower()
    if not host or host in BLOCKED_HOSTS or host.endswith(BLOCKED_SUFFIXES) or "." not in host and ":" not in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:                                    # 2130706433, 0x7f000001, 0177.0.0.1 …: numeric hosts are never a public site here
            ip = ipaddress.ip_address(int(host, 0))
        except ValueError:
            return bool(re.fullmatch(r"[0-9.x]+", host))
    return not ip.is_global or ip.is_multicast


def unsafe_fetch(url: str, own_hosts=(), private_values=()) -> str | None:
    """Why a WebFetch of `url` must not happen, or None. Refused: anything but http(s); the server itself, the local
    network and cloud metadata addresses (server-side request forgery); and URLs that carry data out: a long encoded
    blob, or one of the project's private values (a phone number, an email…)."""
    try:
        u = urllib.parse.urlsplit(str(url).strip())
    except ValueError:
        return "an address that can't be read"
    if u.scheme.lower() not in ("http", "https"):
        return "an address that isn't a web page"
    host = (u.hostname or "").lower()
    if _private_host(host) or host in {h.lower() for h in own_hosts if h}:
        return "a private or local address"
    if u.username or u.password:
        return "an address with a login in it"
    tail = urllib.parse.unquote(u.path + "?" + u.query + "#" + u.fragment)
    if BLOB.search(u.path + "?" + u.query + "#" + u.fragment) or BLOB.search(tail):
        return "an address that carries encoded data"
    flat = re.sub(r"[\s().-]", "", tail.lower())
    for value in private_values:
        v = re.sub(r"[\s().+-]", "", str(value or "").lower())
        if len(v) >= 6 and v in flat:
            return "an address that carries the project's private details"
    return None
