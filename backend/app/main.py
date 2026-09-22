from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import app.models
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
