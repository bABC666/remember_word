"""The schema revision guard must fail closed.

Every ambiguous state is fatal: the application may only start when the codebase
declares exactly one Alembic head, the database records exactly one revision row,
and the two are equal. A warning would let a mismatched process run against real
data, which is the failure this guard exists to prevent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def make_version_db(path: Path, revisions: list[str | None], *, not_null: bool = True) -> Path:
    """A database holding the given ``alembic_version`` rows.

    ``not_null=False`` simulates corruption: Alembic declares the column NOT NULL,
    so a NULL row only exists in a database something has already damaged.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    constraint = " not null" if not_null else ""
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(
            f"create table alembic_version (version_num varchar(32){constraint})"
        )
        for revision in revisions:
            connection.execute("insert into alembic_version values (?)", (revision,))
        connection.commit()
    finally:
        connection.close()
    return path


def make_legacy_version_db(path: Path) -> Path:
    """A database whose alembic_version table has no rows at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("create table alembic_version (version_num varchar(32) not null)")
        connection.commit()
    finally:
        connection.close()
    return path


def test_code_base_declares_exactly_one_head() -> None:
    from app.db import code_head_revision, code_head_revisions

    heads = code_head_revisions()
    assert len(heads) == 1, f"the migration graph must have a single head, found {heads}"
    assert code_head_revision() == heads[0]


def test_matching_single_revision_is_accepted(tmp_path: Path) -> None:
    from app.db import code_head_revision, verify_schema_revision

    database = make_version_db(tmp_path / "app-data" / "ok.db", [code_head_revision()])
    verify_schema_revision(database)  # must not raise


def test_mismatched_revision_is_rejected(tmp_path: Path) -> None:
    from app.db import SchemaRevisionError, verify_schema_revision

    database = make_version_db(tmp_path / "app-data" / "old.db", ["0003_article_reading_tools"])
    with pytest.raises(SchemaRevisionError):
        verify_schema_revision(database)


def test_zero_database_revisions_is_rejected(tmp_path: Path) -> None:
    """An empty version table means the schema state is unknown, not 'fine'."""
    from app.db import SchemaRevisionError, verify_schema_revision

    database = make_legacy_version_db(tmp_path / "app-data" / "empty.db")
    with pytest.raises(SchemaRevisionError):
        verify_schema_revision(database)


def test_multiple_database_revisions_are_rejected(tmp_path: Path) -> None:
    """Two rows mean the version table is corrupt; reading only the first hides it."""
    from app.db import SchemaRevisionError, code_head_revision, verify_schema_revision

    database = make_version_db(
        tmp_path / "app-data" / "two.db",
        [code_head_revision(), "0003_article_reading_tools"],
    )
    with pytest.raises(SchemaRevisionError) as error:
        verify_schema_revision(database)
    assert "2 行" in str(error.value)


def test_a_null_revision_is_rejected(tmp_path: Path) -> None:
    """A single NULL row is a state Alembic never writes, so it is corruption."""
    from app.db import SchemaRevisionError, verify_schema_revision

    database = make_version_db(tmp_path / "app-data" / "null.db", [None], not_null=False)
    with pytest.raises(SchemaRevisionError) as error:
        verify_schema_revision(database)
    assert "1 行" in str(error.value)


def test_an_unusable_row_cannot_be_hidden_behind_a_valid_one(tmp_path: Path) -> None:
    """The fail-open case: dropping the NULL row would leave "exactly one" behind.

    A version table holding the correct revision *and* a NULL row is corrupt, and
    a guard that filters unusable rows before counting reports it as healthy.
    """
    from app.db import SchemaRevisionError, code_head_revision, verify_schema_revision

    database = make_version_db(
        tmp_path / "app-data" / "mixed.db", [code_head_revision(), None], not_null=False
    )
    with pytest.raises(SchemaRevisionError) as error:
        verify_schema_revision(database)
    assert "2 行" in str(error.value)


def test_an_empty_revision_string_is_rejected(tmp_path: Path) -> None:
    from app.db import SchemaRevisionError, verify_schema_revision

    database = make_version_db(tmp_path / "app-data" / "blank.db", [""])
    with pytest.raises(SchemaRevisionError):
        verify_schema_revision(database)


def test_missing_database_file_is_rejected(tmp_path: Path) -> None:
    from app.db import SchemaRevisionError, verify_schema_revision

    with pytest.raises(SchemaRevisionError):
        verify_schema_revision(tmp_path / "app-data" / "absent.db")


def test_missing_version_table_is_rejected(tmp_path: Path) -> None:
    from app.db import SchemaRevisionError, verify_schema_revision

    database = tmp_path / "app-data" / "no-table.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(str(database)).close()
    with pytest.raises(SchemaRevisionError):
        verify_schema_revision(database)


def test_a_path_that_cannot_be_opened_is_rejected(tmp_path: Path) -> None:
    """The guard owns its failure mode: it never leaks a raw sqlite3 error.

    Callers catch ``SchemaRevisionError``; a leaked ``OperationalError`` would
    escape startup handling and let the process continue on an unverified schema.
    """
    from app.db import SchemaRevisionError, verify_schema_revision

    directory = tmp_path / "app-data" / "vocab.db"
    directory.mkdir(parents=True)
    with pytest.raises(SchemaRevisionError):
        verify_schema_revision(directory)


def test_a_file_that_is_not_a_database_is_rejected(tmp_path: Path) -> None:
    from app.db import SchemaRevisionError, verify_schema_revision

    database = tmp_path / "app-data" / "not-sqlite.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    database.write_text("this is not a database", encoding="utf-8")
    with pytest.raises(SchemaRevisionError):
        verify_schema_revision(database)


def test_multiple_code_heads_are_rejected(monkeypatch) -> None:
    """A branched migration graph must stop the app, not silently pass."""
    from app import db as db_module

    monkeypatch.setattr(
        db_module, "code_head_revisions", lambda: ["0007_a", "0007_b"]
    )
    with pytest.raises(db_module.SchemaRevisionError) as error:
        db_module.code_head_revision()
    assert "2" in str(error.value)


def test_zero_code_heads_are_rejected(monkeypatch) -> None:
    from app import db as db_module

    monkeypatch.setattr(db_module, "code_head_revisions", list)
    with pytest.raises(db_module.SchemaRevisionError):
        db_module.code_head_revision()


def test_unreadable_migration_directory_is_rejected(monkeypatch) -> None:
    """A failure to read the migrations must be fatal, never a skipped check."""
    from alembic.script import ScriptDirectory

    from app import db as db_module

    def boom(*_args, **_kwargs):
        raise OSError("migration directory unreadable")

    monkeypatch.setattr(ScriptDirectory, "from_config", staticmethod(boom))
    with pytest.raises(db_module.SchemaRevisionError):
        db_module.code_head_revisions()
