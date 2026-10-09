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

from app.models import (
    AppSetting,
    User,
    UserLexicon,
    UserSession,
    UserSettings,
    UserWordState,
)

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


def test_added_columns_do_not_look_like_modified_rows(database: Path, baseline: dict) -> None:
    """A migration may ADD a compatibility column without that counting as data loss.

    The V1.2 migration adds bridge columns such as ``user_id``. Hashing
    ``select *`` made every existing row look modified for that reason alone,
    which is a false alarm that would train everyone to ignore the verifier.
    """
    connection = sqlite3.connect(str(database))
    connection.execute("alter table word add column user_id integer")
    connection.execute("update word set user_id = 7")
    connection.commit()
    connection.close()

    verified, report = verified_db.compare_against_baseline(database, baseline)
    assert verified, report["failures"]
    assert report["tables"]["word"]["status"] == "ok"


def test_real_content_change_after_an_added_column_still_fails(
    database: Path, baseline: dict
) -> None:
    """Ignoring bookkeeping columns must not blind the verifier to real edits."""
    connection = sqlite3.connect(str(database))
    connection.execute("alter table word add column user_id integer")
    connection.execute("update word set status = 'mastered' where id = 2")
    connection.commit()
    connection.close()

    verified, report = verified_db.compare_against_baseline(database, baseline)
    assert not verified
    assert any("word" in failure and "changed" in failure for failure in report["failures"])


def test_identity_columns_exclude_only_bookkeeping_columns(database: Path) -> None:
    connection = sqlite3.connect(str(database))
    try:
        connection.execute("alter table word add column user_id integer")
        connection.execute("alter table word add column lexicon_entry_id integer")
        wanted = verified_db.identity_columns(connection, "word")
    finally:
        connection.close()
    assert "user_id" not in wanted
    assert "lexicon_entry_id" not in wanted
    assert {"id", "word", "status"} <= set(wanted), wanted
    #review_event has no excluded columns in this fixture shape
    connection = sqlite3.connect(str(database))
    try:
        review_columns = verified_db.identity_columns(connection, "review_event")
    finally:
        connection.close()
    assert "result" in review_columns


def test_v1_2_tables_are_classified() -> None:
    assert verified_db.classify("lexicon_entry") == "immutable"
    assert verified_db.classify("lexicon") == "immutable"
    assert verified_db.classify("user_word_state") == "append_only"
    assert verified_db.classify("review_event") == "append_only"
    assert verified_db.classify("history_event") == "append_only"


def test_recorded_baseline_of_the_real_project_is_usable() -> None:
    """The recorded baseline must describe the database this project ships.

    Phase 2.8 re-recorded it from the verified 0007 production backup, so the
    expectations below are that snapshot and no longer the V1.1 restore source
    this test was originally written against. A baseline recorded from the wrong
    database, or one left behind at a pre-V1.2 revision, fails here.
    """
    path = Path(__file__).resolve().parents[2] / "data/recovery/baseline.json"
    if not path.exists():
        pytest.skip("no project baseline recorded yet")
    baseline = verified_db.load_baseline(path)
    assert baseline["alembic_revision"] == "0007_bridge_foreign_keys"
    assert baseline["integrity_check"] == "ok"
    assert baseline["foreign_key_check_violations"] == 0
    tables = baseline["tables"]
    # Content layer: the imported V1.1 material plus the shared V1.2 lexicon.
    assert tables["word"]["rows"] == 19
    assert tables["lexicon"]["rows"] == 1
    assert tables["lexicon_entry"]["rows"] == 19
    assert tables["import_batch"]["rows"] == 1
    assert tables["import_image"]["rows"] == 2
    assert tables["import_candidate"]["rows"] == 19
    # Learning state and history retained across the 0006/0007 migrations.
    assert tables["user"]["rows"] == 1
    assert tables["user_word_state"]["rows"] == 19
    assert tables["review_event"]["rows"] == 10
    assert tables["article"]["rows"] == 2
    assert tables["article_word_exposure"]["rows"] == 16
    assert tables["article_word_lookup"]["rows"] == 1
    # Without row hashes the verifier could not detect a modified row at all, so
    # a baseline recorded with --no-row-hashes must not pass as the project one.
    assert all("row_hashes" in info for info in tables.values()), "baseline lacks row hashes"


# --- V1.2 runtime tables: mutable columns, and rows that may be pruned ---------
#
# A live database is not a frozen artifact. Logging in rewrites a session's
# ``last_seen_at``, studying rewrites a word's schedule and counters, saving
# settings rewrites ``user_settings``, and ``prune_sessions`` deletes dead session
# rows outright. Pinning any of that would make the verifier fail on ordinary use,
# and a gate that cries wolf is a gate everybody learns to ignore.
#
# The exemptions are deliberately narrow, so these tests guard both directions:
# the mutable columns must NOT be in the row identity, and the columns a rewrite
# would have to forge (``token_hash``, ownership, bridges) must stay in it.

#: Columns that must remain part of each runtime table's row identity.
RUNTIME_IDENTITY_COLUMNS: dict[str, set[str]] = {
    "user": {"id", "username", "created_at"},
    "user_session": {
        "id",
        "user_id",
        "token_hash",
        "created_at",
        "expires_at",
        "user_agent",
    },
    "user_settings": {"user_id", "created_at"},
    "user_lexicon": {"id", "user_id", "lexicon_id", "started_at"},
    "user_word_state": {
        "id",
        "user_id",
        "lexicon_entry_id",
        "legacy_word_id",
        "first_seen",
        "created_at",
    },
    "app_setting": {"key"},
}

RUNTIME_MODELS = (User, UserSession, UserSettings, UserLexicon, UserWordState, AppSetting)


def build_runtime_database(path: Path) -> None:
    """Create the six V1.2 runtime tables from the models' own column lists.

    The columns come from the ORM rather than a hand-written copy, so adding a
    column to one of these models without deciding whether it is mutable fails
    :func:`test_runtime_columns_are_not_part_of_row_identity` instead of silently
    joining the row hash.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    try:
        for model in RUNTIME_MODELS:
            columns = ", ".join(f'"{column.name}"' for column in model.__table__.columns)
            connection.execute(f'create table "{model.__tablename__}" ({columns})')
        connection.commit()
    finally:
        connection.close()


@pytest.fixture()
def runtime_database(tmp_path: Path) -> Path:
    path = tmp_path / "app-data" / "runtime.db"
    build_runtime_database(path)
    return path


def write_rows(path: Path, table: str, rows: list[dict[str, object]]) -> None:
    connection = sqlite3.connect(str(path))
    try:
        for row in rows:
            columns = ", ".join(f'"{name}"' for name in row)
            placeholders = ", ".join("?" for _ in row)
            connection.execute(
                f'insert into "{table}" ({columns}) values ({placeholders})',
                tuple(row.values()),
            )
        connection.commit()
    finally:
        connection.close()


def write_sql(path: Path, statement: str, *parameters: object) -> None:
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(statement, parameters)
        connection.commit()
    finally:
        connection.close()


def session_row(session_id: int, token: str) -> dict[str, object]:
    return {
        "id": session_id,
        "user_id": 1,
        "token_hash": token * 64,
        "created_at": "2026-09-22 11:00:00",
        "expires_at": "2026-10-22 11:00:00",
        "last_seen_at": None,
        "revoked_at": None,
        "user_agent": "pytest",
    }


def state_row(state_id: int, entry_id: int, user_id: int = 1) -> dict[str, object]:
    return {
        "id": state_id,
        "user_id": user_id,
        "lexicon_entry_id": entry_id,
        "legacy_word_id": entry_id,
        "status": "new",
        "first_seen": "2026-09-22 11:00:00",
        "recall_success": 0,
        "recall_fail": 0,
        "consecutive_failures": 0,
        "context_exposure": 0,
        "anchor_override": "",
        "semantic_note": "",
        "notes": "",
        "possible_issue": 0,
        "created_at": "2026-09-22 11:00:00",
        "updated_at": "2026-09-22 11:00:00",
    }


def test_runtime_columns_are_not_part_of_row_identity(runtime_database: Path) -> None:
    """The ignore list must be exactly right in both directions.

    Fails if a listed column does not exist in the model (a typo would silently
    ignore nothing), if a column ordinary use rewrites is still inside the row
    identity, or if a column a rewrite would forge has been dropped from it.
    """
    connection = sqlite3.connect(str(runtime_database))
    try:
        for model in RUNTIME_MODELS:
            table = model.__tablename__
            declared = {column.name for column in model.__table__.columns}
            ignored = verified_db.IGNORED_COLUMNS[table]
            assert ignored <= declared, f"{table}: ignored column is not in the model"
            identity = set(verified_db.identity_columns(connection, table))
            assert identity == RUNTIME_IDENTITY_COLUMNS[table], table
            assert not (identity & ignored), table
    finally:
        connection.close()
    # Only the two tables whose rows live code really deletes may tolerate loss.
    assert verified_db.ROW_TOLERANT_TABLES == frozenset({"user_session", "user_lexicon"})
    assert not (verified_db.ROW_TOLERANT_TABLES - set(RUNTIME_IDENTITY_COLUMNS))


def test_legitimate_session_pruning_verifies_and_is_reported(
    runtime_database: Path,
) -> None:
    """Pruning a dead session and touching ``last_seen_at`` are ordinary use.

    ``prune_sessions`` deletes rows whose session can never be used again, and
    ``touch_session`` rewrites the last-activity stamp. Neither is loss: the
    verifier must still call the database verified, and must still report what
    disappeared rather than accepting it silently.
    """
    write_rows(
        runtime_database,
        "user_session",
        [session_row(1, "a"), session_row(2, "b")],
    )
    baseline = verified_db.capture_baseline(runtime_database, label="runtime")

    write_sql(runtime_database, 'delete from "user_session" where id = 2')
    write_sql(
        runtime_database,
        'update "user_session" set last_seen_at = ? where id = 1',
        "2026-09-23 00:00:00",
    )

    verified, report = verified_db.compare_against_baseline(runtime_database, baseline)
    assert verified, report["failures"]
    assert report["failures"] == []
    entry = report["tables"]["user_session"]
    assert entry["status"] == "rows_pruned"
    assert entry["rows_pruned"] == 1
    assert entry["rows_current"] == 1
    assert any("user_session" in item for item in report["pruned"])


def test_changed_session_token_still_fails(runtime_database: Path) -> None:
    """``token_hash`` stays in the identity: replacing it is a planted backdoor."""
    write_rows(runtime_database, "user_session", [session_row(1, "a")])
    baseline = verified_db.capture_baseline(runtime_database, label="runtime")

    write_sql(
        runtime_database,
        'update "user_session" set token_hash = ? where id = 1',
        "c" * 64,
    )

    verified, report = verified_db.compare_against_baseline(runtime_database, baseline)
    assert not verified
    assert any(
        "user_session" in failure and "changed" in failure
        for failure in report["failures"]
    )
    assert report["tables"]["user_session"]["status"] == "rows_changed"


def test_changed_session_owner_still_fails(runtime_database: Path) -> None:
    """A row kept under the same ID cannot silently change account ownership."""
    write_rows(runtime_database, "user_session", [session_row(1, "a")])
    baseline = verified_db.capture_baseline(runtime_database, label="runtime")

    write_sql(runtime_database, 'update "user_session" set user_id = 2 where id = 1')

    verified, report = verified_db.compare_against_baseline(runtime_database, baseline)
    assert not verified
    assert any(
        "user_session" in failure and "changed" in failure
        for failure in report["failures"]
    )


def test_lost_user_word_state_row_still_fails(runtime_database: Path) -> None:
    """Learning state is not row-tolerant: a lost row is still loss."""
    write_rows(
        runtime_database,
        "user_word_state",
        [state_row(1, 1), state_row(2, 2), state_row(3, 3)],
    )
    baseline = verified_db.capture_baseline(runtime_database, label="runtime")

    write_sql(runtime_database, 'delete from "user_word_state" where id = 3')

    verified, report = verified_db.compare_against_baseline(runtime_database, baseline)
    assert not verified
    assert any("user_word_state" in failure for failure in report["failures"])
    assert report["tables"]["user_word_state"]["status"] == "rows_lost"
    assert report["pruned"] == []


def test_repointed_user_word_state_row_still_fails(runtime_database: Path) -> None:
    """Re-pointing a state at another user is a forged ownership column."""
    write_rows(
        runtime_database,
        "user_word_state",
        [state_row(1, 1, user_id=1), state_row(2, 2, user_id=1)],
    )
    baseline = verified_db.capture_baseline(runtime_database, label="runtime")

    write_sql(runtime_database, 'update "user_word_state" set user_id = 2 where id = 2')

    verified, report = verified_db.compare_against_baseline(runtime_database, baseline)
    assert not verified
    assert any(
        "user_word_state" in failure and "changed" in failure
        for failure in report["failures"]
    )


def test_legitimate_study_and_settings_saves_verify(runtime_database: Path) -> None:
    """A review and a settings save must not look like tampering."""
    write_rows(
        runtime_database,
        "user_word_state",
        [state_row(1, 1), state_row(2, 2)],
    )
    write_rows(
        runtime_database,
        "user_settings",
        [
            {
                "user_id": 1,
                "daily_new_words": 15,
                "article_length": 650,
                "onboarding_seen": 0,
                "theme": "auto",
                "created_at": "2026-09-22 11:00:00",
                "updated_at": "2026-09-22 11:00:00",
            }
        ],
    )
    baseline = verified_db.capture_baseline(runtime_database, label="runtime")

    # what services/study.py::_apply writes on one review ...
    write_sql(
        runtime_database,
        'update "user_word_state" set status = ?, next_review_at = ?, '
        "last_review = ?, consecutive_failures = ?, recall_success = ?, "
        "context_exposure = ?, semantic_note = ?, updated_at = ? where id = 1",
        "familiar",
        "2026-09-23 12:00:00",
        "2026-09-23 00:00:00",
        0,
        1,
        1,
        "第一次在阅读里遇到",
        "2026-09-23 00:00:00",
    )
    # ... and what settings and lexicon selection write for preference changes
    write_sql(
        runtime_database,
        'update "user_settings" set daily_new_words = ?, selected_lexicon_id = ?, '
        'updated_at = ? where user_id = 1',
        30,
        7,
        "2026-09-23 00:00:00",
    )

    verified, report = verified_db.compare_against_baseline(runtime_database, baseline)
    assert verified, report["failures"]
    assert report["tables"]["user_word_state"]["status"] == "ok"
    assert report["tables"]["user_settings"]["status"] == "ok"
