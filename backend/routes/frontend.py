"""Static frontend serving: `/`, static/asset mounts, and the SPA catch-all
fallback for unknown non-API routes.

Not called out in the Task 7 brief's target file structure (which lists only
the domain API route modules), but this logic was route-handler code in the
original main.py and had to move somewhere for main.py to be "short". Kept
as its own module since it's a distinct concern (serving the built frontend)
from any of the domain routers. `mount_static()` must be called explicitly
from main.py (StaticFiles mounts happen on the `app` object itself, not via
`include_router`), and `router` (holding the catch-all `/{full_path:path}`)
must be included LAST, after every other router, or it will shadow them.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

router = APIRouter()

_frontend_dir = Path(__file__).parent.parent.parent / "frontend"
_dist_dir = _frontend_dir / "dist"
_assets_dir = _dist_dir / "assets"

# Prefer the Vite production build (frontend/dist) when present; fall back to
# the legacy dev layout (frontend/index.html + /static) otherwise.
_LIVE_FRONTEND = _assets_dir.is_dir()


def mount_static(app: FastAPI) -> None:
    """Mount the static asset directory on `app`. Call once from main.py."""
    if _LIVE_FRONTEND:
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")
    else:
        app.mount("/static", StaticFiles(directory=str(_frontend_dir)), name="static")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def serve_index() -> HTMLResponse:
    index_path = (_dist_dir if _LIVE_FRONTEND else _frontend_dir) / "index.html"
    return HTMLResponse(content=index_path.read_text())


_API_PREFIXES = (
    "api/",
    "upload/",
    "status/",
    "audit/",
    "v1/",
    "health",
    "metrics/",
    "static/",
    "assets/",
    "docs",
    "redoc",
    "openapi.json",
)


@router.get("/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
def serve_spa_fallback(full_path: str) -> HTMLResponse:
    if full_path.startswith(_API_PREFIXES):
        raise HTTPException(status_code=404, detail="Resource not found")
    index_path = (_dist_dir if _LIVE_FRONTEND else _frontend_dir) / "index.html"
    return HTMLResponse(content=index_path.read_text())
