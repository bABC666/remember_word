"""The physical schema must declare the foreign keys the ORM believes it has.

This is the check that ``PRAGMA foreign_key_check`` cannot make. Migrations 0004
and 0005 added nine bridge columns with ``sa.ForeignKey(...)`` through
``batch_alter_table().add_column()``; SQLite implements that as a plain
``ALTER TABLE ADD COLUMN``, which silently discards the constraint. The ORM then
declared 26 foreign keys while the database enforced 17, and every integrity check
still said "ok" -- a constraint that was never created is never violated.

So the assertion is on ``PRAGMA foreign_key_list``: what is physically there, per
column, with its delete rule. The schema under test is built by running the real
migrations, never by ``create_all()``: the ORM's own metadata is the thing being
audited, so it cannot also be the source of the schema.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import app.models  # noqa: F401  (populates Base.metadata)
from app.db import Base
from tests.conftest import run_alembic

#: The nine columns migrations 0004/0005 added without a physical constraint.
#: Every one is a nullable bridge whose purpose is to preserve history, so all
#: nine must be ON DELETE SET NULL.
BRIDGES: tuple[tuple[str, str, str], ...] = (
    ("word", "user_id", "user"),
    ("word", "lexicon_entry_id", "lexicon_entry"),
    ("review_event", "user_id", "user"),
    ("review_event", "lexicon_entry_id", "lexicon_entry"),
    ("article", "user_id", "user"),
    ("article_word_exposure", "lexicon_entry_id", "lexicon_entry"),
    ("import_batch", "user_id", "user"),
    ("import_candidate", "lexicon_entry_id", "lexicon_entry"),
    ("history_event", "user_id", "user"),
)

NOW = "2026-01-01 00:00:00"


def migrate(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "migrated.db"
    result = run_alembic(database, "upgrade", "head")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return database


@pytest.fixture(scope="module")
def migrated_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A database at head, built by the migrations themselves.

    Read-only tests share one: the introspection is the same for all of them, and
    each migration run costs a subprocess. Tests that write (the delete-rule
    proofs) must not share it, or one test's leftovers become another's failure.
    """
    return migrate(tmp_path_factory.mktemp("fk-matrix") / "app-data")


@pytest.fixture()
def writable_database(tmp_path: Path) -> Path:
    return migrate(tmp_path / "app-data")


def connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(database))
    connection.row_factory = sqlite3.Row
    connection.execute("pragma foreign_keys=ON")
    return connection


def physical_foreign_keys(database: Path) -> dict[tuple[str, str], tuple[str, str]]:
    """column -> (parent table, delete rule) for every physical foreign key."""
    connection = connect(database)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table' order by name"
            )
        ]
        found: dict[tuple[str, str], tuple[str, str]] = {}
        for table in tables:
            for row in connection.execute(f'pragma foreign_key_list("{table}")'):
                found[(table, row["from"])] = (row["table"], row["on_delete"].upper())
        return found
    finally:
        connection.close()


def orm_foreign_keys() -> dict[tuple[str, str], tuple[str, str]]:
    """The same map, declared by SQLAlchemy metadata."""
    declared: dict[tuple[str, str], tuple[str, str]] = {}
    for table in Base.metadata.tables.values():
        for column in table.columns:
            for foreign_key in column.foreign_keys:
                declared[(table.name, column.name)] = (
                    foreign_key.column.table.name,
                    (foreign_key.ondelete or "NO ACTION").upper(),
                )
    return declared


def test_the_two_maps_are_not_trivially_empty(migrated_database: Path) -> None:
    """Guard against a vacuous comparison: both sides must be substantial."""
    assert len(orm_foreign_keys()) >= 20
    assert len(physical_foreign_keys(migrated_database)) >= 20


def test_every_orm_foreign_key_exists_physically(migrated_database: Path) -> None:
    declared = orm_foreign_keys()
    physical = physical_foreign_keys(migrated_database)

    missing = {key: expected for key, expected in declared.items() if key not in physical}
    assert not missing, (
        "these foreign keys are declared by the ORM but are not in the database, so "
        f"nothing enforces them: {sorted(missing.items())}"
    )


def test_no_undeclared_foreign_key_exists_physically(migrated_database: Path) -> None:
    declared = orm_foreign_keys()
    physical = physical_foreign_keys(migrated_database)

    extra = {key: value for key, value in physical.items() if key not in declared}
    assert not extra, (
        f"the database has constraints the ORM does not declare: {sorted(extra.items())}"
    )


def test_no_delete_rule_disagrees(migrated_database: Path) -> None:
    """A wrong ON DELETE is as dangerous as a missing constraint.

    ``CASCADE`` where the ORM says ``SET NULL`` deletes learning history when a
    user is removed.
    """
    declared = orm_foreign_keys()
    physical = physical_foreign_keys(migrated_database)

    mismatched = {
        key: (declared[key], physical[key])
        for key in declared.keys() & physical.keys()
        if declared[key] != physical[key]
    }
    assert not mismatched, f"delete-rule/parent mismatches: {sorted(mismatched.items())}"


@pytest.mark.parametrize(("table", "column", "parent"), BRIDGES)
def test_bridge_columns_are_set_null(
    migrated_database: Path, table: str, column: str, parent: str
) -> None:
    assert physical_foreign_keys(migrated_database)[(table, column)] == (parent, "SET NULL")


def test_foreign_key_check_is_clean(migrated_database: Path) -> None:
    connection = connect(migrated_database)
    try:
        assert connection.execute("pragma foreign_key_check").fetchall() == []
        assert connection.execute("pragma integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()


def insert_user(connection: sqlite3.Connection, username: str) -> int:
    connection.execute(
        "insert into user (username, display_name, password_hash, role, is_active, "
        "created_at, updated_at) values (?, ?, '!', 'admin', 1, ?, ?)",
        (username, username, NOW, NOW),
    )
    return connection.execute(
        "select id from user where username = ?", (username,)
    ).fetchone()[0]


def insert_word(connection: sqlite3.Connection, word: str, **bridges: int) -> int:
    columns = {
        "word": word,
        "phonetic": "",
        "part_of_speech": "",
        "source_meanings": "[]",
        "source_raw": word,
        "anchor": "",
        "semantic_note": "",
        "status": "new",
        "first_seen": NOW,
        "recall_success": 0,
        "recall_fail": 0,
        "consecutive_failures": 0,
        "context_exposure": 0,
        "possible_issue": 0,
        "notes": "",
        "created_at": NOW,
        "updated_at": NOW,
        **bridges,
    }
    names = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    connection.execute(
        f"insert into word ({names}) values ({placeholders})", tuple(columns.values())
    )
    return connection.execute("select last_insert_rowid()").fetchone()[0]


def test_deleting_a_user_nulls_the_bridge_and_keeps_the_word(writable_database: Path) -> None:
    """Behavioural proof of the delete rule, not just its declaration.

    The word must survive with its bridge cleared. A missing constraint would also
    let this pass, which is why the pragma matrix above is asserted as well; a
    ``CASCADE`` would delete the row, which is the data loss being guarded against.
    """
    connection = connect(writable_database)
    try:
        user_id = insert_user(connection, "fk-probe")
        word_id = insert_word(connection, "fk-probe-word", user_id=user_id)
        connection.commit()

        connection.execute("delete from user where id = ?", (user_id,))
        connection.commit()

        row = connection.execute(
            "select id, word, user_id from word where id = ?", (word_id,)
        ).fetchone()
        assert row is not None, "deleting the user deleted the word's history"
        assert row["word"] == "fk-probe-word"
        assert row["user_id"] is None, "the bridge must be cleared, not left dangling"
    finally:
        connection.close()


def test_deleting_a_lexicon_entry_nulls_the_review_bridge(writable_database: Path) -> None:
    """The review survives; only its pointer to the deleted entry is cleared."""
    connection = connect(writable_database)
    try:
        connection.execute(
            "insert into lexicon (owner_user_id, name, description, visibility, source_type, "
            "entry_count, created_at, updated_at) values (null, 'fk-probe-lex', '', 'public', "
            "'fk_probe', 1, ?, ?)",
            (NOW, NOW),
        )
        lexicon_id = connection.execute(
            "select id from lexicon where source_type = 'fk_probe'"
        ).fetchone()[0]
        connection.execute(
            "insert into lexicon_entry (lexicon_id, word, normalized_word, phonetic, "
            "part_of_speech, source_meanings, source_raw, default_anchor, semantic_note, "
            "possible_issue, created_at, updated_at) values (?, 'probe', 'probe', '', '', "
            "'[]', 'x', '', '', 0, ?, ?)",
            (lexicon_id, NOW, NOW),
        )
        entry_id = connection.execute(
            "select id from lexicon_entry where lexicon_id = ?", (lexicon_id,)
        ).fetchone()[0]
        word_id = insert_word(connection, "fk-probe-probe", lexicon_entry_id=entry_id)
        connection.execute(
            "insert into review_event (word_id, timestamp, result, source, article_id, "
            "status_before, status_after, review_type, lexicon_entry_id) values (?, ?, 'know', "
            "'daily', null, 'new', 'learning', 'recall', ?)",
            (word_id, NOW, entry_id),
        )
        connection.commit()
        event_id = connection.execute(
            "select id from review_event where lexicon_entry_id = ?", (entry_id,)
        ).fetchone()[0]

        connection.execute("delete from lexicon_entry where id = ?", (entry_id,))
        connection.commit()

        row = connection.execute(
            "select id, lexicon_entry_id from review_event where id = ?", (event_id,)
        ).fetchone()
        assert row is not None, "deleting a lexicon entry deleted the user's review history"
        assert row["lexicon_entry_id"] is None
        word = connection.execute(
            "select id, lexicon_entry_id from word where id = ?", (word_id,)
        ).fetchone()
        assert word is not None
        assert word["lexicon_entry_id"] is None
    finally:
        connection.close()
