import sqlite3
from pathlib import Path


def test_daily_backup_creates_once_without_overwriting(tmp_path: Path) -> None:
    from app.services.backup import create_backup

    database = tmp_path / "vocab.db"
    backups = tmp_path / "backups"
    with sqlite3.connect(database) as connection:
        connection.execute("create table marker(value text)")
        connection.execute("insert into marker values ('first')")

    first = create_backup(database, backups, name="2026-09-21-vocab.db")
    first_bytes = first.read_bytes()

    with sqlite3.connect(database) as connection:
        connection.execute("insert into marker values ('second')")

    second = create_backup(database, backups, name="2026-09-21-vocab.db")
    assert second == first
    assert first.read_bytes() == first_bytes
