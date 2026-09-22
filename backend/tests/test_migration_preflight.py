"""Legacy words that collide after normalization must stop the migration.

Migration 0005 keys V1.2 lexicon entries on ``strip().casefold()``, a constraint
V1.1 never enforced. Two legacy rows that normalize identically would make the
second ``UserWordState`` violate ``UNIQUE(user_id, lexicon_entry_id)`` and abort
the migration *halfway*, after columns had been added and tables created -- a
database that is neither 0004 nor 0005 and that cannot be migrated again.

These tests pin three things:

* a collision aborts the migration and reports every colliding row;
* it aborts *before the first statement*, so the database is untouched and a
  second attempt fails the same way instead of wedging on existing objects;
* words that merely look similar migrate normally, so the preflight does not
  over-trigger and block legitimate data.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

NOW = "2026-01-01 00:00:00"

#: Every NOT NULL column of the V1.1 ``word`` table without a server default.
INSERT_WORD = (
    "insert into word (word, phonetic, part_of_speech, source_meanings, source_raw, "
    "anchor, semantic_note, status, first_seen, recall_success, recall_fail, "
    "consecutive_failures, context_exposure, possible_issue, notes, created_at, "
    "updated_at) values (?, '', '', '[]', ?, '', '', 'new', ?, 0, 0, 0, 0, 0, '', ?, ?)"
)


def connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(database))
    connection.row_factory = sqlite3.Row
    return connection


def add_words(database: Path, words: list[str]) -> list[int]:
    connection = connect(database)
    try:
        ids = []
        for word in words:
            cursor = connection.execute(INSERT_WORD, (word, word, NOW, NOW, NOW))
            ids.append(cursor.lastrowid)
        connection.commit()
        return ids
    finally:
        connection.close()


def admin_user_id(database: Path) -> int:
    """The admin account the V1.1 rows get attributed to.

    Migration 0004 already bootstraps one, so it is fetched rather than created:
    the point of this helper is to assert the migration found it, not to invent a
    second admin.
    """
    connection = connect(database)
    try:
        row = connection.execute(
            "select id from user where role = 'admin' order by id limit 1"
        ).fetchone()
        assert row is not None, "0004 must bootstrap an admin account"
        user_id = row[0]
        if (
            connection.execute(
                "select count(*) from user_settings where user_id = ?", (user_id,)
            ).fetchone()[0]
            == 0
        ):
            connection.execute(
                "insert into user_settings (user_id, created_at, updated_at) values (?, ?, ?)",
                (user_id, NOW, NOW),
            )
            connection.commit()
        return user_id
    finally:
        connection.close()


def revision(database: Path) -> str:
    connection = connect(database)
    try:
        return connection.execute("select version_num from alembic_version").fetchone()[0]
    finally:
        connection.close()


def table_names(database: Path) -> set[str]:
    connection = connect(database)
    try:
        return {
            row[0]
            for row in connection.execute("select name from sqlite_master where type='table'")
        }
    finally:
        connection.close()


def column_names(database: Path, table: str) -> list[str]:
    connection = connect(database)
    try:
        return [row[1] for row in connection.execute(f'pragma table_info("{table}")')]
    finally:
        connection.close()


def scalar(database: Path, statement: str):
    connection = connect(database)
    try:
        return connection.execute(statement).fetchone()[0]
    finally:
        connection.close()


@pytest.fixture()
def schema_at_0004(alembic_database):
    """A database migrated to 0004, the revision just before the lexicon work."""
    result = alembic_database("upgrade", "0004_multiuser_foundation")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert revision(alembic_database.database) == "0004_multiuser_foundation"
    return alembic_database


def test_case_collision_aborts_without_touching_the_database(schema_at_0004) -> None:
    database = schema_at_0004.database
    ids = add_words(database, ["Colour", "colour"])
    before_tables = table_names(database)
    before_columns = column_names(database, "word")

    result = schema_at_0004("upgrade", "head")

    assert result.returncode != 0, "a normalized collision must not migrate"
    assert "RuntimeError" in result.stderr, result.stderr
    assert "迁移中止" in result.stderr, result.stderr
    # The report must name the collision and every row involved, or a human
    # cannot resolve it.
    assert "'colour'" in result.stderr, result.stderr
    for word_id, word in zip(ids, ("Colour", "colour"), strict=True):
        assert f"id={word_id}" in result.stderr, result.stderr
        assert f"word={word!r}" in result.stderr, result.stderr

    # Nothing changed: not the revision, not the schema, not a single column.
    assert revision(database) == "0004_multiuser_foundation"
    assert table_names(database) == before_tables
    assert column_names(database, "word") == before_columns
    assert "user_id" not in before_columns, "0005 must not have added its columns"
    assert scalar(database, "select count(*) from word") == 2
    assert scalar(
        database, "select count(*) from history_event where event_type = 'v12_migration'"
    ) == 0


def test_second_attempt_fails_the_same_way_instead_of_wedging(schema_at_0004) -> None:
    """A failed migration must leave a database that can be retried.

    When a migration aborts after its DDL, the revision still says 0004 while the
    new tables already exist, and every later attempt dies on "table already
    exists" -- unfixable without manual surgery. Running the preflight first is
    what makes the failure repeatable and diagnosable.
    """
    database = schema_at_0004.database
    add_words(database, ["Colour", "colour"])

    first = schema_at_0004("upgrade", "head")
    second = schema_at_0004("upgrade", "head")

    assert first.returncode != 0
    assert second.returncode != 0
    assert "迁移中止" in second.stderr, second.stderr
    assert "already exists" not in second.stderr, second.stderr
    assert "duplicate column" not in second.stderr, second.stderr
    assert revision(database) == "0004_multiuser_foundation"
    assert "lexicon" not in table_names(database)


def test_whitespace_collision_is_detected(schema_at_0004) -> None:
    """``strip()`` is part of the normalization, so padding collides too."""
    database = schema_at_0004.database
    add_words(database, ["apple", "apple "])

    result = schema_at_0004("upgrade", "head")

    assert result.returncode != 0
    assert "'apple'" in result.stderr, result.stderr
    assert revision(database) == "0004_multiuser_foundation"


def test_every_row_of_a_three_way_collision_is_reported(schema_at_0004) -> None:
    database = schema_at_0004.database
    ids = add_words(database, ["Word", "word", "word "])

    result = schema_at_0004("upgrade", "head")

    assert result.returncode != 0
    for word_id in ids:
        assert f"id={word_id}" in result.stderr, result.stderr
    # Exactly one collision group is reported, not one line per row.
    assert result.stderr.count("normalized=") == 1, result.stderr


def test_similar_but_distinct_words_migrate(schema_at_0004) -> None:
    """The preflight must not block words that only look alike.

    Without this control, a preflight that rejected everything would pass every
    other test in this module.
    """
    database = schema_at_0004.database
    admin_id = admin_user_id(database)
    add_words(database, ["Colour", "color", "Apple ", "apple-pie"])

    result = schema_at_0004("upgrade", "head")

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    from app.db import code_head_revision

    assert revision(database) == code_head_revision()
    assert scalar(database, "select count(*) from lexicon_entry") == 4
    assert scalar(database, "select count(*) from user_word_state") == 4
    # Every legacy row keeps its identity and gains both bridges.
    assert scalar(database, "select count(*) from word where user_id is null") == 0
    assert scalar(database, "select count(*) from word where lexicon_entry_id is null") == 0
    assert scalar(database, "select count(distinct user_id) from word") == 1
    assert scalar(database, "select min(user_id) from word") == admin_id
