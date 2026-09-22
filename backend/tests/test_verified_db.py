"""Tests for the baseline-snapshot backup verifier.

A verifier that cannot fail is worthless, so every failure mode it claims to
detect is exercised here: lost rows, changed rows, missing tables, integrity
problems, and the legitimate growth it must NOT flag.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
_spec = importlib.util.spec_from_file_location("verified_db", TOOLS / "verified_db.py")
assert _spec and _spec.loader
verified_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verified_db)


def build_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.executescript(
        """
        create table alembic_version (version_num varchar(32) not null);
        insert into alembic_version values ('0003_article_reading_tools');
        create table word (id integer primary key, word text, status text);
        create table review_event (id integer primary key, word_id integer, result text);
        create table history_event (id integer primary key, event_type text);
        create table article_word_lookup (id integer primary key, surface text);
        """
    )
    connection.executemany(
        "insert into word values (?,?,?)",
        [(1, "quit", "learning"), (2, "signal", "familiar"), (3, "motion", "familiar")],
    )
    connection.executemany(
        "insert into review_event values (?,?,?)",
        [(1, 1, "fail"), (2, 1, "fuzzy"), (3, 2, "know")],
    )
    connection.execute("insert into history_event values (1, 'manual_backup')")
    connection.commit()
    connection.close()


@pytest.fixture()
def database(tmp_path: Path) -> Path:
    path = tmp_path / "app-data" / "baseline-test.db"
    build_database(path)
    return path


@pytest.fixture()
def baseline(database: Path, tmp_path: Path) -> dict:
    snapshot = verified_db.capture_baseline(database, label="test baseline")
    verified_db.save_baseline(snapshot, tmp_path / "baseline.json")
    return snapshot


def test_capture_baseline_records_counts_and_row_hashes(
    database: Path, baseline: dict
) -> None:
    assert baseline["integrity_check"] == "ok"
    assert baseline["foreign_key_check_violations"] == 0
    assert baseline["alembic_revision"] == "0003_article_reading_tools"
    assert baseline["tables"]["word"]["rows"] == 3
    assert baseline["tables"]["review_event"]["rows"] == 3
    assert len(baseline["tables"]["word"]["row_hashes"]) == 3


def test_classification_separates_content_from_history(
    database: Path, baseline: dict
) -> None:
    assert baseline["tables"]["word"]["classification"] == "immutable"
    assert baseline["tables"]["review_event"]["classification"] == "append_only"
    assert baseline["tables"]["history_event"]["classification"] == "append_only"
    assert baseline["tables"]["article_word_lookup"]["classification"] == "append_only"


def test_identical_database_verifies(database: Path, baseline: dict) -> None:
    verified, report = verified_db.compare_against_baseline(database, baseline)
    assert verified, report["failures"]


def test_legitimate_growth_verifies_and_is_reported(database: Path, baseline: dict) -> None:
    connection = sqlite3.connect(str(database))
    connection.execute("insert into word values (4, 'export', 'new')")
    connection.execute("insert into review_event values (4, 4, 'know')")
    connection.execute("insert into history_event values (2, 'user_login')")
    connection.commit()
    connection.close()

    verified, report = verified_db.compare_against_baseline(database, baseline)
    assert verified, report["failures"]
    assert any("word" in item for item in report["growth"])
    assert any("review_event" in item for item in report["growth"])


def test_lost_rows_in_append_only_table_fail(database: Path, baseline: dict) -> None:
    """History must never shrink, even in a table that is allowed to grow."""
    connection = sqlite3.connect(str(database))
    connection.execute("delete from review_event where id = 2")
    connection.commit()
    connection.close()

    verified, report = verified_db.compare_against_baseline(database, baseline)
    assert not verified
    assert any("review_event" in failure for failure in report["failures"])


def test_lost_rows_in_immutable_table_fail(database: Path, baseline: dict) -> None:
    connection = sqlite3.connect(str(database))
    connection.execute("delete from word where id = 3")
    connection.commit()
    connection.close()

    verified, report = verified_db.compare_against_baseline(database, baseline)
    assert not verified
    assert any("word" in failure for failure in report["failures"])


def test_modified_rows_fail_even_when_counts_match(database: Path, baseline: dict) -> None:
    """A silent UPDATE is the subtlest way to lose history."""
    connection = sqlite3.connect(str(database))
    connection.execute("update review_event set result = 'know' where id = 1")
    connection.commit()
    connection.close()

    verified, report = verified_db.compare_against_baseline(database, baseline)
    assert not verified
    assert any("changed" in failure for failure in report["failures"])
    assert report["tables"]["review_event"]["status"] == "rows_changed"


def test_modified_immutable_content_fails(database: Path, baseline: dict) -> None:
    connection = sqlite3.connect(str(database))
    connection.execute("update word set status = 'mastered' where id = 2")
    connection.commit()
    connection.close()

    verified, report = verified_db.compare_against_baseline(database, baseline)
    assert not verified
    assert any("word" in failure for failure in report["failures"])


def test_missing_table_fails(database: Path, baseline: dict) -> None:
    connection = sqlite3.connect(str(database))
    connection.execute("drop table article_word_lookup")
    connection.commit()
    connection.close()

    verified, report = verified_db.compare_against_baseline(database, baseline)
    assert not verified
    assert any("missing" in failure for failure in report["failures"])


def test_wrong_revision_fails(database: Path, baseline: dict) -> None:
    connection = sqlite3.connect(str(database))
    connection.execute("update alembic_version set version_num = '0001_initial'")
    connection.commit()
    connection.close()

    verified, report = verified_db.compare_against_baseline(database, baseline)
    assert not verified
    assert any("revision" in failure for failure in report["failures"])

    # Revision drift can be tolerated explicitly when that is the intent.
    verified_ignoring, _ = verified_db.compare_against_baseline(
        database, baseline, require_revision=False
    )
    assert verified_ignoring


def test_missing_database_fails(tmp_path: Path, baseline: dict) -> None:
    verified, report = verified_db.compare_against_baseline(
        tmp_path / "nope.db", baseline
    )
    assert not verified
    assert "does not exist" in report["failures"][0]


def test_baseline_round_trips_through_json(tmp_path: Path, baseline: dict) -> None:
    path = tmp_path / "snap.json"
    verified_db.save_baseline(baseline, path)
    loaded = verified_db.load_baseline(path)
    assert loaded["tables"]["word"]["row_hashes"] == baseline["tables"]["word"]["row_hashes"]


def test_unsupported_baseline_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"baseline_version": 999}', encoding="utf-8")
    with pytest.raises(ValueError):
        verified_db.load_baseline(path)


def test_verifier_never_writes_to_the_database(database: Path, baseline: dict) -> None:
    before = verified_db.sha256_file(database)
    verified_db.compare_against_baseline(database, baseline)
    verified_db.capture_baseline(database)
    assert verified_db.sha256_file(database) == before


def test_recorded_baseline_of_the_real_project_is_usable() -> None:
    """The committed baseline must describe the restored V1.1 database."""
    path = Path(__file__).resolve().parents[2] / "data/recovery/baseline.json"
    if not path.exists():
        pytest.skip("no project baseline recorded yet")
    baseline = verified_db.load_baseline(path)
    assert baseline["alembic_revision"] == "0003_article_reading_tools"
    assert baseline["tables"]["word"]["rows"] == 19
    assert baseline["tables"]["review_event"]["rows"] == 10
    assert baseline["tables"]["article"]["rows"] == 1
    assert baseline["tables"]["article_word_exposure"]["rows"] == 16
    assert baseline["tables"]["article_word_lookup"]["rows"] == 0
    assert baseline["tables"]["import_batch"]["rows"] == 1
    assert baseline["tables"]["import_image"]["rows"] == 2
    assert baseline["tables"]["import_candidate"]["rows"] == 19
