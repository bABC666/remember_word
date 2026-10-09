"""SQLite online snapshots; managed retention is opt-in through run_backup."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.testing_guards import assert_not_real_data

MANAGER = "shici-daily-backup-v1"
DAILY_NAME = re.compile(r"shici-daily-\d{4}-\d{2}-\d{2}\.db\Z")


class BackupBusy(RuntimeError):
    pass


class BackupInvalid(RuntimeError):
    pass


@contextmanager
def backup_lock(directory: Path, *, wait_seconds: float = 0):
    """OS locks release on process death. Never unlink the shared lock inode."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".backup.lock").open("a+b") as lock:
        if lock.seek(0, os.SEEK_END) == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise BackupBusy("backup_busy") from exc
                time.sleep(0.1)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def inspect_backup(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise BackupInvalid("not_regular_backup")
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise BackupInvalid("backup_has_sidecars")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise BackupInvalid("integrity_failed")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise BackupInvalid("foreign_key_failed")
        revision = []
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='alembic_version'").fetchone():
            revision = [
                row[0]
                for row in db.execute(
                    "SELECT version_num FROM alembic_version ORDER BY version_num"
                )
            ]
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "schema_revision": revision,
        "size_bytes": path.stat().st_size,
        "sha256": digest,
        "integrity_check": "ok",
        "foreign_key_violations": 0,
    }


def _snapshot(source: Path, target: Path) -> dict:
    """Read the live WAL normally; only the completed standalone copy is immutable."""
    assert_not_real_data(source, action="back up")
    if not source.is_file() or source.stat().st_size == 0:
        raise BackupInvalid("source_missing_or_empty")
    deadline = time.monotonic() + 300

    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise BackupInvalid("snapshot_timeout")

    with (
        closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src,
        closing(sqlite3.connect(target)) as dst,
    ):
        src.backup(dst, pages=256, progress=progress, sleep=0.1)
        dst.execute("PRAGMA journal_mode=DELETE")
    with target.open("r+b") as stream:
        os.fsync(stream.fileno())
    return inspect_backup(target)


def _remove_partial(path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(path) + suffix).unlink(missing_ok=True)


def create_backup(database_path: Path, backups_dir: Path, name: str | None = None) -> Path:
    """Compatibility entry for startup/UI: no retention of legacy or manual names."""
    name = name or f"{datetime.now().astimezone().date().isoformat()}-vocab.db"
    if Path(name).name != name or name in {".", ".."}:
        raise BackupInvalid("invalid_name")
    target = backups_dir / name
    if target.resolve() == database_path.resolve():
        raise BackupInvalid("source_is_target")
    with backup_lock(backups_dir, wait_seconds=30):
        if target.exists():
            inspect_backup(target)
            return target
        temporary = backups_dir / f".{uuid.uuid4().hex}.partial"
        try:
            _snapshot(database_path, temporary)
            os.replace(temporary, target)
        finally:
            _remove_partial(temporary)
    return target


def _event(directory: Path, status: str, **fields) -> None:
    # Do not log exception messages, SQL rows, source paths or environment values.
    event = {"time": datetime.now(UTC).isoformat(), "status": status, **fields}
    line = json.dumps(event, ensure_ascii=True)
    print(line, file=sys.stderr, flush=True)
    try:
        with (directory / "backup-events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
    except OSError:
        # stderr remains available to systemd/journald even with a full disk.
        print('{"status": "log_file_unavailable"}', file=sys.stderr, flush=True)


def _valid_managed(path: Path, source_id: str) -> dict:
    metadata_path = path.with_suffix(".json")
    if metadata_path.is_symlink():
        raise BackupInvalid("metadata_symlink")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or any(
        (
            metadata.get("manager") != MANAGER,
            metadata.get("kind") != "daily",
            metadata.get("source_id") != source_id,
            metadata.get("filename") != path.name,
        )
    ):
        raise BackupInvalid("unmanaged_backup")
    facts = inspect_backup(path)
    if any(metadata.get(key) != value for key, value in facts.items()):
        raise BackupInvalid("metadata_mismatch")
    return metadata


def _prune(directory: Path, source_id: str, keep: int, current: Path) -> None:
    valid = []
    for path in directory.iterdir():
        if not DAILY_NAME.fullmatch(path.name):
            continue
        try:
            _valid_managed(path, source_id)
        except (OSError, ValueError, sqlite3.Error, BackupInvalid):
            _event(directory, "retention_skipped", reason="invalid_or_unmanaged")
            continue
        valid.append(path)
    # Keep current even after a clock rollback. Otherwise newest calendar days win.
    valid.sort(key=lambda path: (path == current, path.name), reverse=True)
    for path in valid[keep:]:
        path.unlink()
        path.with_suffix(".json").unlink()
        _event(directory, "pruned", filename=path.name)


def run_backup(
    database_path: Path,
    backups_dir: Path,
    *,
    kind: str = "daily",
    keep: int = 30,
    timezone: str = "UTC",
    now: datetime | None = None,
) -> Path:
    """Publish DB then manifest under one lock; orphaned files fail closed on rerun."""
    stage = "configuration"
    try:
        if keep < 1 or kind not in {"daily", "manual"}:
            raise BackupInvalid("invalid_configuration")
        clock = now or datetime.now(UTC if timezone == "UTC" else ZoneInfo(timezone))
        if clock.tzinfo is None:
            raise BackupInvalid("naive_time")
        source = database_path.resolve()
        assert_not_real_data(source, action="back up")
        source_id = hashlib.sha256(os.path.normcase(str(source)).encode()).hexdigest()
        name = (
            f"shici-daily-{clock.date().isoformat()}.db"
            if kind == "daily"
            else f"shici-manual-{clock.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex}.db"
        )
        target = backups_dir / name
        if source == target.resolve():
            raise BackupInvalid("source_is_target")
        stage = "lock"
        with backup_lock(backups_dir):
            stage = "source"
            if not source.is_file() or source.stat().st_size == 0:
                raise BackupInvalid("source_missing_or_empty")
            stage = "existing"
            manifest = target.with_suffix(".json")
            if target.exists() or manifest.exists() or target.is_symlink():
                _valid_managed(target, source_id)
                _event(backups_dir, "already_exists", filename=name)
                return target  # no new backup, no cleanup
            temporary = backups_dir / f".{uuid.uuid4().hex}.partial"
            staged_manifest = temporary.with_suffix(".partial.json")
            try:
                stage = "snapshot_validation"
                facts = _snapshot(source, temporary)
                metadata = {
                    "manager": MANAGER,
                    "kind": kind,
                    "filename": name,
                    "source_id": source_id,
                    "started_at": clock.isoformat(),
                    "completed_at": datetime.now(UTC).isoformat(),
                    **facts,
                }
                stage = "publish"
                with staged_manifest.open("x", encoding="utf-8") as stream:
                    json.dump(metadata, stream, ensure_ascii=True, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
                os.replace(staged_manifest, manifest)
                if os.name != "nt":
                    fd = os.open(backups_dir, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
            finally:
                _remove_partial(temporary)
                staged_manifest.unlink(missing_ok=True)
            _event(
                backups_dir,
                "created",
                filename=name,
                sha256=facts["sha256"],
                size_bytes=facts["size_bytes"],
            )
            stage = "retention"
            if kind == "daily":
                _prune(backups_dir, source_id, keep, target)
        return target
    except Exception as exc:
        _event(backups_dir, "failed", stage=stage, error_type=type(exc).__name__)
        raise


def configure_parser(parser) -> None:
    parser.add_argument("--database", default=os.getenv("VOCAB_DATABASE_PATH"))
    parser.add_argument("--backups-dir", default=os.getenv("VOCAB_BACKUPS_DIR"))
    parser.add_argument("--kind", choices=("daily", "manual"), default="daily")
    parser.add_argument("--keep", default=os.getenv("VOCAB_BACKUP_KEEP", "30"))
    parser.add_argument("--timezone", default=os.getenv("VOCAB_BACKUP_TIMEZONE", "UTC"))
    parser.set_defaults(func=command_backup, file_only_preview=True)


def command_backup(args) -> int:
    if not args.database or not args.backups_dir:
        print(
            '{"status": "failed", "stage": "configuration", "reason": "explicit_paths_required"}',
            file=sys.stderr,
        )
        return 2
    try:
        keep = int(args.keep)
    except ValueError:
        _event(Path(args.backups_dir), "failed", stage="configuration", reason="invalid_keep")
        return 2
    try:
        run_backup(
            Path(args.database),
            Path(args.backups_dir),
            kind=args.kind,
            keep=keep,
            timezone=args.timezone,
        )
        return 0
    except BackupBusy:
        return 3
    except Exception:  # noqa: BLE001 - run_backup logs redacted failure; CLI must not leak traceback
        return 1
