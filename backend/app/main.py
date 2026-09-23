from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import app.models
from app import csrf
from app.api import articles, auth, dashboard, imports, lexicons, settings, study, words
from app.config import get_settings
from app.db import get_session_factory, verify_schema_revision
from app.services.auth import prune_sessions
from app.services.backup import create_backup
from app.services.ocr import configure_paddle_environment
from app.services.userdata import NotFoundError


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config = get_settings()
    config.ensure_directories()
    # Schema evolves only through Alembic. Startup must never create or alter
    # tables, otherwise model edits silently pre-create columns that a later
    # migration then fails to add.
    verify_schema_revision(config.database_path)
    if config.ocr_enabled:
        configure_paddle_environment()
    create_backup(config.database_path, config.backups_dir)
    # Housekeeping after the backup, so the day's snapshot still contains whatever
    # this removes. Only sessions that can never be accepted again are deleted
    # (revoked, absolutely expired, idle past VOCAB_SESSION_IDLE_DAYS); the rule is
    # the exact complement of the check every request goes through, so a session
    # that is still valid is never touched. A failure here stops startup on
    # purpose: the same policy as verify_schema_revision and create_backup above.
    with get_session_factory()() as session:
        prune_sessions(session)
    yield


app = FastAPI(title="拾词", version="1.0.0", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(imports.router)
app.include_router(study.router)
app.include_router(words.router)
app.include_router(articles.router)
app.include_router(lexicons.router)
app.include_router(settings.router)


@app.exception_handler(NotFoundError)
async def _not_found_handler(_request: Request, error: NotFoundError) -> JSONResponse:
    """A resource that is missing and one that belongs to somebody else both
    answer 404 with the same message, so responses cannot be compared to probe
    for the existence of another user's data."""
    return JSONResponse(status_code=404, content={"detail": str(error)})


@app.middleware("http")
async def _no_store_auth_responses(request: Request, call_next):
    """Keep authentication responses out of every cache.

    ``/api/auth/*`` answers describe the caller's identity and session, so they are
    never reusable: a shared cache must not replay them, and a browser must not
    serve a cached "signed in" body after the session ended.

    Deliberately scoped to that path group rather than applied site-wide, and
    implemented as middleware rather than as a router dependency so it also covers
    responses FastAPI builds for a rejected request -- a 401 raised by
    ``get_current_user`` never reaches a route's own dependencies.
    """
    response = await call_next(request)
    if request.url.path.startswith("/api/auth/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def _same_origin_write_check(request: Request, call_next):
    """Refuse write requests that did not come from this application's own origin.

    The third of the three defences described in :mod:`app.csrf`. The cookie's
    ``SameSite=Lax`` keeps a cross-site request from carrying a session at all, and the
    JSON-only body parser keeps a cross-site form from building a valid request, but
    neither covers a same-site page on another origin, Chromium's two-minute
    "Lax + POST" window, or a future endpoint that accepts a form body.

    It is registered after the ``no-store`` middleware, so it is the outermost one: a
    rejected request never reaches a route dependency, which means no session is
    resolved, no ``last_seen_at`` is touched and nothing is written. Safe methods,
    ``/assets`` and the SPA fallback are all GETs and are untouched, and ``/docs``
    keeps working because "Try it out" is same-origin.

    ``POST /api/auth/login`` is checked as well, deliberately: a forged login signs the
    victim into the attacker's account, and that request carries no session cookie of
    its own to be protected by anything else.
    """
    config = get_settings()
    if not csrf.write_is_allowed(
        method=request.method,
        origin=request.headers.get("origin", ""),
        referer=request.headers.get("referer", ""),
        host_header=request.headers.get("host", ""),
        scheme=request.url.scheme,
        allow_missing_origin=config.csrf_allow_missing_origin,
        trusted_origins=config.csrf_trusted_origins,
    ):
        return JSONResponse(
            status_code=403,
            # Generic on purpose: the rejected value is caller-controlled and is never
            # echoed back or recorded. ``no-store`` for the same reason the auth
            # responses carry it -- this answer must never be reused by a cache.
            content={"detail": "请求来源不可信，已拒绝"},
            headers={"Cache-Control": "no-store"},
        )
    return await call_next(request)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": "拾词"}


frontend_dist = get_settings().frontend_dist
assets_dir = frontend_dist / "assets"
if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str) -> FileResponse:
    index = frontend_dist / "index.html"
    requested = frontend_dist / path
    if requested.is_file() and frontend_dist in requested.resolve().parents:
        return FileResponse(requested)
    if index.exists():
        return FileResponse(index)
    raise HTTPException(404, "前端尚未构建，请先运行 npm run build")
