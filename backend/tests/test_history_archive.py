"""A committed archive may explain exact history rows, never general data loss."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verified_db = load_tool("verified_db")


@pytest.fixture()
def world(tmp_path: Path) -> tuple[Path, Path, Path, dict]:
    database = tmp_path / "app-data" / "current.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            create table alembic_version (version_num text not null);
            insert into alembic_version values ('0007_bridge_foreign_keys');
            create table word (id integer primary key, word text not null);
            insert into word values (1, 'signal');
            create table history_event (
                id integer primary key, user_id integer, event_type text not null,
                timestamp text not null, entity_type text not null,
                entity_id integer, payload text not null
            );
            insert into history_event values
                (1, 7, 'login_failed', '2024-01-01 00:00:00', 'user', 7, '{"username":"a"}'),
                (2, 7, 'user_updated', '2024-01-02 00:00:00', 'user', 7, '{"role":"admin"}'),
                (3, null, 'user_login', '2024-01-03 00:00:00', 'user', null, '{}');
            """
        )
    baseline = verified_db.capture_baseline(database)
    backup = tmp_path / "app-data" / "before.db"
    with sqlite3.connect(database) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    evidence = tmp_path / "evidence"
    return database, backup, evidence, baseline


def delete(database: Path, statement: str) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(statement)


def test_committed_archive_explains_only_its_exact_history_id(world) -> None:
    database, backup, evidence, baseline = world
    history_archive = load_tool("history_archive")
    history_archive.create_committed_archive(backup, evidence, baseline, [1], run_id="run-001")
    delete(database, "delete from history_event where id = 1")

    ordinary, _ = verified_db.compare_against_baseline(database, baseline)
    verified, report = verified_db.compare_against_baseline(
        database, baseline, retention_dir=evidence
    )

    assert not ordinary
    assert verified, report["failures"]
    assert report["history_event_archived"]["ids"] == ["1"]


def make_archive(world, ids=(1,)) -> Path:
    _database, backup, evidence, baseline = world
    return load_tool("history_archive").create_committed_archive(
        backup, evidence, baseline, list(ids), run_id="run-001"
    )


@pytest.mark.parametrize("fault", ["missing", "pending", "tampered_archive", "missing_backup", "path_escape"])
def test_invalid_evidence_never_excuses_deleted_history(world, fault: str) -> None:
    database, _backup, evidence, baseline = world
    manifest_path = make_archive(world)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if fault == "missing":
        manifest_path.unlink()
    elif fault == "pending":
        manifest["state"] = "prepared"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif fault == "tampered_archive":
        (evidence / manifest["archive_file"]).write_text("changed", encoding="utf-8")
    elif fault == "missing_backup":
        (evidence / manifest["pre_backup_file"]).unlink()
    else:
        manifest["archive_file"] = "../elsewhere.jsonl"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    delete(database, "delete from history_event where id = 1")

    verified, report = verified_db.compare_against_baseline(
        database, baseline, retention_dir=evidence
    )
    assert not verified
    assert report["failures"]


def test_invalid_evidence_path_fails_closed(world) -> None:
    database, _backup, evidence, baseline = world
    evidence.write_text("not a directory", encoding="utf-8")
    delete(database, "delete from history_event where id = 1")

    verified, report = verified_db.compare_against_baseline(
        database, baseline, retention_dir=evidence
    )
    assert not verified
    assert any("archive evidence invalid" in failure for failure in report["failures"])


def test_archived_id_cannot_be_reused_with_different_contents(world) -> None:
    database, _backup, evidence, baseline = world
    make_archive(world)
    with sqlite3.connect(database) as connection:
        connection.execute("delete from history_event where id = 1")
        connection.execute(
            "insert into history_event values (1, 8, 'user_login', "
            "'2024-01-01 00:00:00', 'user', 8, '{}')"
        )

    verified, report = verified_db.compare_against_baseline(
        database, baseline, retention_dir=evidence
    )
    assert not verified
    assert any("history_event" in failure for failure in report["failures"])


def test_archive_conflict_checks_full_row_not_only_baseline_identity(world) -> None:
    database, _backup, evidence, baseline = world
    make_archive(world)
    # user_id is deliberately excluded from the old baseline identity, but an
    # archived row and a live row with the same ID must be byte-for-byte equal.
    with sqlite3.connect(database) as connection:
        connection.execute("update history_event set user_id = 8 where id = 1")

    verified, report = verified_db.compare_against_baseline(
        database, baseline, retention_dir=evidence
    )
    assert not verified
    assert any("archived id 1 conflicts" in failure for failure in report["failures"])


def test_archive_does_not_excuse_another_history_id_or_other_table(world) -> None:
    database, _backup, evidence, baseline = world
    make_archive(world)
    delete(database, "delete from history_event where id in (1, 2)")
    delete(database, "delete from word where id = 1")

    verified, report = verified_db.compare_against_baseline(
        database, baseline, retention_dir=evidence
    )
    assert not verified
    assert any("history_event" in failure for failure in report["failures"])
    assert any("word" in failure for failure in report["failures"])


def test_archive_contains_all_columns_and_exact_hashes(world) -> None:
    database, backup, evidence, _baseline = world
    before = verified_db.sha256_file(database)
    manifest_path = make_archive(world)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive_lines = (evidence / manifest["archive_file"]).read_text(encoding="utf-8").splitlines()

    assert manifest["format_version"] == 1
    assert manifest["state"] == "committed"
    assert manifest["row_ids"] == [1]
    assert manifest["pre_backup_sha256"] == verified_db.sha256_file(backup)
    assert json.loads(archive_lines[0])["columns"] == [
        "id", "user_id", "event_type", "timestamp", "entity_type", "entity_id", "payload"
    ]
    assert json.loads(archive_lines[1])["values"] == [
        1, 7, "login_failed", "2024-01-01 00:00:00", "user", 7, '{"username":"a"}'
    ]
    assert verified_db.sha256_file(database) == before


def test_post_baseline_history_is_protected_by_archive_checkpoint(world) -> None:
    database, backup, evidence, baseline = world
    with sqlite3.connect(database) as connection:
        connection.execute(
            "insert into history_event values "
            "(4, null, 'user_login', '2024-02-01 00:00:00', '', null, '{}')"
        )
    with sqlite3.connect(database) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    make_archive(world, ids=(4,))
    delete(database, "delete from history_event where id = 4")

    verified, report = verified_db.compare_against_baseline(
        database, baseline, retention_dir=evidence
    )
    assert verified, report["failures"]

    delete(database, "delete from history_event where id = 3")
    verified, report = verified_db.compare_against_baseline(
        database, baseline, retention_dir=evidence
    )
    assert not verified


def test_verify_backup_uses_committed_evidence_by_default(world, tmp_path: Path) -> None:
    database, backup, _evidence, baseline = world
    baseline_path = tmp_path / "baseline.json"
    verified_db.save_baseline(baseline, baseline_path)
    evidence = tmp_path / "history-retention"
    load_tool("history_archive").create_committed_archive(
        backup, evidence, baseline, [1], run_id="run-001"
    )
    delete(database, "delete from history_event where id = 1")

    result = subprocess.run(
        [sys.executable, str(TOOLS / "verify_backup.py"), str(database),
         "--baseline", str(baseline_path)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "history_event_archived" in result.stdout


@pytest.mark.parametrize("field", ["baseline_digest", "full_hashes", "row_ids", "pre_history_identity_hashes"])
def test_manifest_field_tampering_is_rejected(world, field: str) -> None:
    database, _backup, evidence, baseline = world
    manifest_path = make_archive(world)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if field in {"full_hashes", "pre_history_identity_hashes"}:
        manifest[field]["1"] = "0" * 64
    elif field == "row_ids":
        manifest[field] = [2]
    else:
        manifest[field] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    delete(database, "delete from history_event where id = 1")

    verified, report = verified_db.compare_against_baseline(
        database, baseline, retention_dir=evidence
    )
    assert not verified
    assert report["failures"]


def test_rehashed_archive_cannot_disagree_with_pre_backup(world) -> None:
    database, _backup, evidence, baseline = world
    manifest_path = make_archive(world)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive_path = evidence / manifest["archive_file"]
    lines = archive_path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[1])
    row["values"][-1] = '{"username":"forged"}'
    archive_path.write_text(lines[0] + "\n" + json.dumps(row) + "\n", encoding="utf-8")
    manifest["archive_sha256"] = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    delete(database, "delete from history_event where id = 1")

    verified, report = verified_db.compare_against_baseline(
        database, baseline, retention_dir=evidence
    )
    assert not verified
    assert report["failures"]


def test_pre_backup_with_loss_in_another_table_cannot_authorize_archive(world) -> None:
    database, backup, evidence, baseline = world
    make_archive(world)
    with sqlite3.connect(backup) as connection:
        connection.execute("delete from word where id = 1")
    # Even if the altered pre-backup hash is also written into the manifest,
    # it is not a trusted source from which to justify history loss.
    manifest_path = evidence / "run-001.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stored_backup = evidence / manifest["pre_backup_file"]
    with sqlite3.connect(stored_backup) as connection:
        connection.execute("delete from word where id = 1")
    manifest["pre_backup_sha256"] = hashlib.sha256(stored_backup.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    delete(database, "delete from history_event where id = 1")

    verified, report = verified_db.compare_against_baseline(
        database, baseline, retention_dir=evidence
    )
    assert not verified
    assert any("archive evidence invalid" in failure for failure in report["failures"])


def test_empty_baseline_table_is_not_mistaken_for_missing_hashes(world) -> None:
    database, backup, evidence, _baseline = world
    with sqlite3.connect(database) as connection:
        connection.execute("create table article_word_lookup (id integer primary key, surface text)")
    baseline = verified_db.capture_baseline(database)
    with sqlite3.connect(database) as source, sqlite3.connect(backup) as target:
        source.backup(target)

    manifest_path = load_tool("history_archive").create_committed_archive(
        backup, evidence, baseline, [1], run_id="run-001"
    )
    assert manifest_path.is_file()


def test_two_archives_keep_post_baseline_checkpoint_strict(world) -> None:
    database, backup, evidence, baseline = world
    make_archive(world)
    delete(database, "delete from history_event where id = 1")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "insert into history_event values "
            "(4, null, 'user_login', '2024-02-01 00:00:00', '', null, '{}')"
        )
    with sqlite3.connect(database) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    load_tool("history_archive").create_committed_archive(
        backup, evidence, baseline, [3], run_id="run-002"
    )
    delete(database, "delete from history_event where id = 3")

    verified, report = verified_db.compare_against_baseline(
        database, baseline, retention_dir=evidence
    )
    assert verified, report["failures"]
    assert report["history_event_archived"]["ids"] == ["1", "3"]

    delete(database, "delete from history_event where id = 4")
    verified, report = verified_db.compare_against_baseline(
        database, baseline, retention_dir=evidence
    )
    assert not verified
    assert any("checkpoint row 4" in failure for failure in report["failures"])


def test_missing_earlier_manifest_cannot_erase_post_baseline_history(world) -> None:
    database, backup, evidence, baseline = world
    with sqlite3.connect(database) as connection:
        connection.execute(
            "insert into history_event values "
            "(4, null, 'user_login', '2024-02-01 00:00:00', '', null, '{}')"
        )
    with sqlite3.connect(database) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    first = make_archive(world, ids=(4,))
    delete(database, "delete from history_event where id = 4")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "insert into history_event values "
            "(5, null, 'user_login', '2024-03-01 00:00:00', '', null, '{}')"
        )
    with sqlite3.connect(database) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    load_tool("history_archive").create_committed_archive(
        backup, evidence, baseline, [5], run_id="run-002"
    )
    delete(database, "delete from history_event where id = 5")
    first.unlink()

    verified, report = verified_db.compare_against_baseline(
        database, baseline, retention_dir=evidence
    )
    assert not verified
    assert any("archive evidence invalid" in failure for failure in report["failures"])
