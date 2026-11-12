"""The HTML pages: landing or dashboard, projects, studio, agents and teams, marketplace, profile, login, legal,
guide, and the admins' users and analytics pages."""

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse

from accounts import plans

from state import load_meta
from web import STATIC, current_user

router = APIRouter()


@router.get("/")
async def home(request: Request):
    """A confirmed account gets its dashboard; everyone else gets the landing page."""
    user = request.state.user
    page = "dashboard.html" if user and user["role"] != "unconfirmed" else "landing.html"
    # The same address answers differently once you log in or out, so a browser must never reuse a saved copy.
    return FileResponse(STATIC / page, headers={"Cache-Control": "no-store"})


@router.get("/studio")
async def studio():
    # The studio only opens through a project (/p/<uuid>); old links land on the projects list.
    return RedirectResponse("/projects")


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(STATIC / "logo.svg", media_type="image/svg+xml")


@router.get("/projects/new")
async def new_project_page():
    return FileResponse(STATIC / "new-project.html")


@router.get("/projects")
async def projects_page():
    return FileResponse(STATIC / "projects.html")


def _own_agents_page(request: Request, page: str):
    """My agents and My teams are for plans with your own agents (Pro, Agency): Starter members hire from the marketplace."""
    if not plans.of(current_user(request)).get("custom_agents"):
        return RedirectResponse("/hire", status_code=303)
    return FileResponse(STATIC / page)


@router.get("/agents")
@router.get("/agents/{rest:path}")
async def agents_page(request: Request, rest: str = ""):
    return _own_agents_page(request, "agents.html")


@router.get("/teams")
@router.get("/teams/{rest:path}")
async def teams_page(request: Request, rest: str = ""):
    return _own_agents_page(request, "teams.html")


@router.get("/guide")
async def guide_page():
    return FileResponse(STATIC / "guide.html")


@router.get("/profile")
async def profile_page():
    return FileResponse(STATIC / "profile.html")


@router.get("/login")
@router.get("/register")
@router.get("/forgot")
async def auth_pages(request: Request):
    user = request.state.user
    if user:
        return RedirectResponse("/confirm" if user["role"] == "unconfirmed" else "/projects", status_code=303)
    return FileResponse(STATIC / "auth.html")


@router.get("/confirm")
async def confirm_page(request: Request):
    user = request.state.user
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user["role"] != "unconfirmed":
        return RedirectResponse("/projects", status_code=303)
    return FileResponse(STATIC / "auth.html")


@router.get("/terms")
@router.get("/privacy")
async def legal_page():
    return FileResponse(STATIC / "legal.html")


@router.get("/analytics")
async def analytics_page(request: Request):
    if request.state.user["role"] != "admin":
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC / "analytics.html")


@router.get("/users")
async def users_page(request: Request):
    if request.state.user["role"] != "admin":
        return RedirectResponse("/projects", status_code=303)
    return FileResponse(STATIC / "users.html")


@router.get("/p/{pid}")
async def project_page(pid: str):
    load_meta(pid)
    return FileResponse(STATIC / "studio.html")


@router.get("/hire")
async def hire_page():
    return FileResponse(STATIC / "hire.html")


@router.get("/hire/agents/{pk}")
@router.get("/hire/teams/{pk}")
async def hire_profile_page(pk: str):
    return FileResponse(STATIC / "hire-profile.html")
