"""Synthetic databases only: never resolve application default settings."""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.services import backup


def source_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
            CREATE TABLE alembic_version(version_num TEXT);
            INSERT INTO alembic_version VALUES ('synthetic_revision');
            CREATE TABLE parent(id INTEGER PRIMARY KEY);
            CREATE TABLE child(parent_id INTEGER REFERENCES parent(id));
            INSERT INTO parent VALUES (1);
            INSERT INTO child VALUES (1);
        """)


def run_cli(tmp_path, *args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "app.cli", "backup", *map(str, args)],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "VOCAB_DATA_DIR": str(tmp_path / "unused"), **(env or {})},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=25,
        check=False,
    )


def test_wal_snapshot_metadata_and_standalone_restore(tmp_path):
    source = tmp_path / "source.db"
    source_db(source)
    with closing(sqlite3.connect(source)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO parent VALUES (2)")
        writer.commit()
        assert Path(str(source) + "-wal").stat().st_size > 0
        result = run_cli(tmp_path, "--database", source, "--backups-dir", tmp_path / "backups")
        assert result.returncode == 0, result.stderr
        target = next((tmp_path / "backups").glob("shici-daily-*.db"))
        metadata = json.loads(target.with_suffix(".json").read_text())
        assert metadata["schema_revision"] == ["synthetic_revision"]
        assert metadata["size_bytes"] == target.stat().st_size
        assert metadata["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
        assert datetime.fromisoformat(metadata["completed_at"]).tzinfo is not None
        restored = tmp_path / "restore" / "vocab.db"
        restored.parent.mkdir()
        shutil.copyfile(target, restored)
        with closing(sqlite3.connect(restored)) as db:
            assert db.execute("SELECT id FROM parent ORDER BY id").fetchall() == [(1,), (2,)]
            assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert not Path(str(target) + "-wal").exists()
        before = target.read_bytes()
        assert (
            run_cli(tmp_path, "--database", source, "--backups-dir", target.parent).returncode == 0
        )
        assert target.read_bytes() == before


def test_retention_only_valid_managed_daily_and_never_manual(tmp_path):
    source = tmp_path / "source.db"
    source_db(source)
    directory = tmp_path / "backups"
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for day in range(33):
        backup.run_backup(source, directory, now=start + timedelta(days=day))
    assert len(list(directory.glob("shici-daily-*.db"))) == 30
    assert not (directory / "shici-daily-2026-01-03.db").exists()
    manual = backup.run_backup(source, directory, kind="manual")
    legacy = directory / "2020-01-01-vocab.db"
    shutil.copyfile(manual, legacy)
    foreign = directory / "shici-daily-2000-01-01.db"
    shutil.copyfile(manual, foreign)  # a matching filename alone never grants ownership
    damaged = directory / "shici-daily-2026-01-04.db"
    damaged.write_bytes(b"not a database")
    original = {p.name: p.read_bytes() for p in (manual, legacy, foreign, damaged)}
    backup.run_backup(source, directory, now=start + timedelta(days=34))
    for name, content in original.items():
        assert (directory / name).read_bytes() == content
    assert len(list(directory.glob("shici-daily-*.db"))) == 32


@pytest.mark.parametrize("problem", ["missing", "corrupt", "foreign_key"])
def test_failure_nonzero_redacted_and_no_retention(tmp_path, problem):
    source = tmp_path / "secret-source.db"
    source_db(source)
    directory = tmp_path / "backups"
    backup.run_backup(source, directory, now=datetime(2020, 1, 1, tzinfo=UTC))
    before = {p.name: p.read_bytes() for p in directory.glob("*.db")}
    if problem == "missing":
        source.unlink()
    elif problem == "corrupt":
        source.write_bytes(b"secret-password: malformed")
    else:
        with closing(sqlite3.connect(source)) as db:
            db.execute("INSERT INTO child VALUES (999)")
            db.commit()
    result = run_cli(tmp_path, "--database", source, "--backups-dir", directory, "--keep", "1")
    assert result.returncode != 0
    assert {p.name: p.read_bytes() for p in directory.glob("*.db")} == before
    log = (directory / "backup-events.jsonl").read_text()
    assert '"status": "failed"' in log
    assert "secret" not in log + result.stderr
    assert not list(directory.glob("*.partial*"))
    if problem == "missing":
        assert not source.exists()


def test_process_lock_refuses_competing_cli_and_recovers(tmp_path):
    source = tmp_path / "source.db"
    source_db(source)
    directory = tmp_path / "backups"
    directory.mkdir()
    with backup.backup_lock(directory):
        result = run_cli(tmp_path, "--database", source, "--backups-dir", directory)
        assert result.returncode == 3
        assert not list(directory.glob("*.db"))
    assert run_cli(tmp_path, "--database", source, "--backups-dir", directory).returncode == 0


def test_explicit_environment_and_manual_backups(tmp_path):
    source = tmp_path / "source.db"
    source_db(source)
    directory = tmp_path / "backups"
    env = {"VOCAB_DATABASE_PATH": str(source), "VOCAB_BACKUPS_DIR": str(directory)}
    for _ in range(2):
        result = run_cli(tmp_path, "--kind", "manual", env=env)
        assert result.returncode == 0, result.stderr
    assert len(list(directory.glob("shici-manual-*.db"))) == 2
    assert not (tmp_path / "unused").exists()


def test_legacy_service_refuses_missing_source(tmp_path):
    source = tmp_path / "missing.db"
    with pytest.raises(backup.BackupInvalid):
        backup.create_backup(source, tmp_path / "backups")
    assert not source.exists()


def test_publish_failure_keeps_old_backups_and_refuses_orphan(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    source_db(source)
    directory = tmp_path / "backups"
    old = backup.run_backup(source, directory, now=datetime(2020, 1, 1, tzinfo=UTC))
    real_replace = os.replace

    def fail_manifest(src, dst):
        if Path(dst).suffix == ".json":
            raise OSError("secret-storage-detail")
        return real_replace(src, dst)

    monkeypatch.setattr(backup.os, "replace", fail_manifest)
    with pytest.raises(OSError):
        backup.run_backup(source, directory, keep=1)
    assert old.exists()
    assert not list(directory.glob("*.partial*"))
    monkeypatch.setattr(backup.os, "replace", real_replace)
    assert run_cli(tmp_path, "--database", source, "--backups-dir", directory).returncode != 0
    assert old.exists()
    assert "secret-storage-detail" not in (directory / "backup-events.jsonl").read_text()


def test_invalid_metadata_never_deleted_or_overwritten(tmp_path):
    source = tmp_path / "source.db"
    source_db(source)
    directory = tmp_path / "backups"
    target = backup.run_backup(source, directory)
    metadata = target.with_suffix(".json")
    metadata.write_text("{}")
    before = target.read_bytes()
    assert run_cli(tmp_path, "--database", source, "--backups-dir", directory).returncode != 0
    assert target.read_bytes() == before
    assert metadata.read_text() == "{}"


def test_unwritable_destination_reports_to_stderr(tmp_path):
    source = tmp_path / "source.db"
    source_db(source)
    directory = tmp_path / "not-a-directory"
    directory.write_text("preserve")
    result = run_cli(tmp_path, "--database", source, "--backups-dir", directory)
    assert result.returncode != 0
    assert '"status": "failed"' in result.stderr
    assert '"status": "log_file_unavailable"' in result.stderr
    assert directory.read_text() == "preserve"


def test_process_death_releases_lock(tmp_path):
    source = tmp_path / "source.db"
    source_db(source)
    directory = tmp_path / "backups"
    code = (
        "import os, sys; from pathlib import Path; "
        "from app.services.backup import backup_lock; "
        "lock = backup_lock(Path(sys.argv[1])); lock.__enter__(); os._exit(17)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(directory)],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        timeout=10,
    )
    assert result.returncode == 17
    assert run_cli(tmp_path, "--database", source, "--backups-dir", directory).returncode == 0


def test_startup_backup_waits_for_scheduler_lock(tmp_path):
    source = tmp_path / "source.db"
    source_db(source)
    directory = tmp_path / "backups"
    code = (
        "import sys; from pathlib import Path; "
        "from app.services.backup import create_backup; "
        "print('ready', flush=True); sys.stdin.readline(); "
        "create_backup(Path(sys.argv[1]), Path(sys.argv[2]))"
    )
    with backup.backup_lock(directory):
        child = subprocess.Popen(
            [sys.executable, "-c", code, str(source), str(directory)],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            assert child.stdout.readline().strip() == "ready"
            child.stdin.write("go\n")
            child.stdin.flush()
            with pytest.raises(subprocess.TimeoutExpired):
                child.wait(timeout=0.5)
        except BaseException:
            child.kill()
            child.communicate()
            raise
    _, stderr = child.communicate(timeout=10)
    assert child.returncode == 0, stderr
    assert len(list(directory.glob("*-vocab.db"))) == 1


def test_no_implicit_application_database(tmp_path):
    result = run_cli(tmp_path, env={"VOCAB_DATABASE_PATH": "", "VOCAB_BACKUPS_DIR": ""})
    assert result.returncode == 2
    assert not (tmp_path / "unused").exists()


def test_standalone_runner_uses_same_backup_service(tmp_path):
    source = tmp_path / "source.db"
    source_db(source)
    directory = tmp_path / "backups"
    script = Path(__file__).resolve().parents[2] / "tools" / "backup.py"
    result = subprocess.run(
        [sys.executable, str(script), "--database", str(source), "--backups-dir", str(directory)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=25,
    )
    assert result.returncode == 0, result.stderr
    assert len(list(directory.glob("shici-daily-*.db"))) == 1


@pytest.mark.parametrize(
    "option,value", [("--keep", "0"), ("--keep", "secret"), ("--timezone", "invalid-secret-zone")]
)
def test_invalid_configuration_has_no_backup_or_leaked_values(tmp_path, option, value):
    source = tmp_path / "source.db"
    source_db(source)
    directory = tmp_path / "backups"
    result = run_cli(tmp_path, "--database", source, "--backups-dir", directory, option, value)
    assert result.returncode != 0
    assert "secret" not in result.stderr
    assert not list(directory.glob("*.db"))
