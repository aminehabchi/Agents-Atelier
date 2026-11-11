"""The three plans. Members pay Anthropic for the AI themselves (their own API key); a plan only unlocks features.

Admins have every feature. The limits are enforced by the server; the pages only explain them.
"""

PLANS = {
    "starter": {
        "name": "Starter", "price": 0,
        "projects": 1,              # at most this many projects (None = no limit)
        "custom_agents": False,     # create, duplicate and edit your own agents and teams
        "publish": False,           # publish agents and teams to the marketplace
        "research": False,          # agents with web search (Research)
        "browser_checks": "critic", # "critic": only before the Critic · "all": before Responsive, Accessibility, QA and the Critic
        "versions": 10,             # versions kept per project
        "credit": True,             # the downloaded .zip carries a small "Made with Agents Atelier" line
        "run_limit": 3.0,           # most a single run may spend on the member's Claude account, in USD
        "run_limit_max": 3.0,       # …and the most the member may set it to
    },
    "pro": {
        "name": "Pro", "price": 9,
        "projects": None, "custom_agents": True, "publish": False, "research": True, "browser_checks": "all",
        "versions": 100, "credit": False, "run_limit": 5.0, "run_limit_max": 50.0,
    },
    "agency": {
        "name": "Agency", "price": 29,
        "projects": None, "custom_agents": True, "publish": True, "research": True, "browser_checks": "all",
        "versions": None, "credit": False, "run_limit": 10.0, "run_limit_max": 200.0,
    },
}
DEFAULT = "starter"
ADMIN = {**PLANS["agency"], "name": "Admin", "run_limit_max": 1000.0}


def of(user: dict | None) -> dict:
    """The features of this member's plan (no user: an old project without owner, run by an admin)."""
    if not user or user.get("role") == "admin":
        return ADMIN
    return PLANS.get(user.get("plan") or DEFAULT, PLANS[DEFAULT])


def run_limit(user: dict | None) -> float:
    """The spending limit of one run: the member's own choice within the plan's range, else the plan's default."""
    p = of(user)
    chosen = (user or {}).get("run_limit")
    return min(float(chosen), p["run_limit_max"]) if chosen else p["run_limit"]


def rank(plan: str) -> int:
    return list(PLANS).index(plan) if plan in PLANS else 0


def public() -> list[dict]:
    return [{"id": k, **v} for k, v in PLANS.items()]


class PlanError(Exception):
    """A feature the member's plan doesn't include. The message says which plan has it."""

    def __init__(self, text: str, needs: str = "pro"):
        super().__init__(text)
        self.needs = needs


def need(user: dict | None, feature: str, text: str):
    if not of(user)[feature]:
        raise PlanError(text, "agency" if feature == "publish" else "pro")
