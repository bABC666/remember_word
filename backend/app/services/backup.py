from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


def create_backup(database_path: Path, backups_dir: Path, name: str | None = None) -> Path:
    backups_dir.mkdir(parents=True, exist_ok=True)
    target = backups_dir / (name or f"{datetime.now().astimezone().date().isoformat()}-vocab.db")
    if target.exists():
        return target
    if not database_path.exists():
        database_path.parent.mkdir(parents=True, exist_ok=True)
        sqlite3.connect(database_path).close()
    with sqlite3.connect(database_path) as source, sqlite3.connect(target) as destination:
        source.backup(destination)
    return target
