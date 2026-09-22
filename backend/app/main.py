from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import app.models
from app.api import articles, dashboard, imports, settings, study, words
from app.config import get_settings
from app.db import verify_schema_revision
from app.services.backup import create_backup
from app.services.ocr import configure_paddle_environment


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
    yield


app = FastAPI(title="拾词", version="1.0.0", lifespan=lifespan)
app.include_router(dashboard.router)
app.include_router(imports.router)
app.include_router(study.router)
app.include_router(words.router)
app.include_router(articles.router)
app.include_router(settings.router)


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
