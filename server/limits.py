"""Sign-up protection: how often one IP address may create accounts, log in or ask for a reset, and which email
domains are throwaway inboxes.

Counters live in memory: they reset when the server restarts, and each server process keeps its own. That is
enough to stop a script on one machine; a busy public deployment would move them to Postgres or Redis.
"""

import time
from collections import defaultdict, deque
from pathlib import Path

import config

DISPOSABLE_FILE = Path(__file__).resolve().parent / "disposable_domains.txt"


class Limit:
    """At most `count` events per `seconds` for one key (an IP address)."""

    def __init__(self, count: int, seconds: int):
        self.count, self.seconds = count, seconds
        self.hits: dict[str, deque] = defaultdict(deque)

    def wait(self, key: str) -> int:
        """Seconds until `key` may try again (0 = it may, and the try is counted)."""
        if not self.count:
            return 0
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] >= self.seconds:
            q.popleft()
        if len(q) >= self.count:
            return int(self.seconds - (now - q[0])) + 1
        q.append(now)
        if len(self.hits) > 50_000:            # forget idle addresses so memory stays bounded
            for k in [k for k, v in self.hits.items() if not v][:10_000]:
                del self.hits[k]
        return 0


SIGNUP = Limit(config.SIGNUPS_PER_HOUR, 3600)
LOGIN = Limit(config.LOGINS_PER_15_MIN, 900)
RESET = Limit(config.RESETS_PER_HOUR, 3600)


def client_ip(request) -> str:
    """The visitor's address. Behind a reverse proxy (TRUST_PROXY), the first X-Forwarded-For entry."""
    if config.TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client else "unknown"


def wait_text(seconds: int) -> str:
    minutes = max(1, -(-seconds // 60))
    return f"{minutes} minute{'s' if minutes > 1 else ''}"


def _load_domains() -> set[str]:
    domains = set()
    for path in (DISPOSABLE_FILE, config.DISPOSABLE_DOMAINS_FILE):
        if path and Path(path).is_file():
            for line in Path(path).read_text().splitlines():
                line = line.strip().lower()
                if line and not line.startswith("#"):
                    domains.add(line)
    return domains


DISPOSABLE = _load_domains()


def is_disposable(email: str) -> bool:
    if not config.BLOCK_DISPOSABLE_EMAILS:
        return False
    domain = email.rsplit("@", 1)[-1].lower()
    parts = domain.split(".")
    return any(".".join(parts[i:]) in DISPOSABLE for i in range(len(parts) - 1))   # sub.mailinator.com too
