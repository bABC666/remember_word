"""Tests for the data-safety guards.

These tests exist because the V1.1 database was destroyed by a test fixture that
ran ``Base.metadata.drop_all`` against the application engine after it had
resolved to the real ``data/`` directory. The guards must be proven to fire,
not merely assumed to exist.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from app.db import database_path_from_url, make_engine
from app.testing_guards import (
    UnsafeDatabasePathError,
    assert_not_real_data,
    assert_safe_for_destructive_operation,
    is_real_data_path,
    is_test_process,
    is_test_scratch_path,
)

REAL_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def test_tests_run_in_test_mode() -> None:
    assert is_test_process() is True, "pytest must be detectable as a test process"


def test_real_data_directory_is_recognised() -> None:
    assert is_real_data_path(REAL_DATA_DIR / "vocab.db") is True
    assert is_real_data_path(REAL_DATA_DIR / "vocab.db-wal") is True
    assert is_real_data_path(REAL_DATA_DIR / "vocab.db-shm") is True
    assert is_real_data_path(REAL_DATA_DIR) is True
    # Anything under the declared real data root is real user data.
    assert is_real_data_path(REAL_DATA_DIR / "backups" / "2026-09-22-vocab.db") is True
    assert is_real_data_path(REAL_DATA_DIR / "recovery" / "vocab-restored-v1.1.db") is True


def test_temporary_paths_are_not_treated_as_real_data(tmp_path: Path) -> None:
    assert is_real_data_path(tmp_path / "vocab.db") is False
    assert is_real_data_path(tmp_path / "app-data" / "vocab.db") is False
    assert is_test_scratch_path(tmp_path / "vocab.db") is True


def test_binding_an_engine_to_real_data_is_refused() -> None:
    with pytest.raises(UnsafeDatabasePathError) as error:
        make_engine(f"sqlite:///{(REAL_DATA_DIR / 'vocab.db').as_posix()}")
    assert "REFUSING" in str(error.value)


def test_resolving_settings_for_real_data_is_refused() -> None:
    """The exact failure that destroyed the database must now be impossible.

    Removing every override leaves the settings resolving to the project's real
    ``data/`` directory, which is what the fixture used to do silently.
    """
    from app import config

    saved_data = os.environ.pop("VOCAB_DATA_DIR", None)
    saved_real = os.environ.pop("VOCAB_REAL_DATA_DIR", None)
    config.get_settings.cache_clear()
    try:
        with pytest.raises(UnsafeDatabasePathError):
            config.get_settings()
    finally:
        # Restore the isolated environment so later tests are unaffected.
        os.environ["VOCAB_DATA_DIR"] = str(_conftest_data_dir())
        os.environ["VOCAB_REAL_DATA_DIR"] = str(REAL_DATA_DIR)
        if saved_real is not None:
            os.environ["VOCAB_REAL_DATA_DIR"] = saved_real
        config.get_settings.cache_clear()
        config.get_settings()
        if saved_data is not None:
            os.environ["VOCAB_DATA_DIR"] = saved_data


def _conftest_data_dir() -> Path:
    import tests.conftest as conftest_module

    return conftest_module._SESSION_DATA_DIR


def test_destructive_operations_are_refused_outside_test_scratch(tmp_path: Path) -> None:
    with pytest.raises(UnsafeDatabasePathError):
        assert_safe_for_destructive_operation(
            REAL_DATA_DIR / "vocab.db", action="drop every table in"
        )


def test_destructive_operations_are_allowed_in_test_scratch(tmp_path: Path) -> None:
    # Must not raise.
    assert_safe_for_destructive_operation(
        tmp_path / "app-data" / "vocab.db", action="drop every table in"
    )


def test_guard_is_inert_in_a_normal_process() -> None:
    """Non-test processes must still be able to open the real database."""
    saved = {name: os.environ.pop(name) for name in ("PYTEST_CURRENT_TEST", "PYTEST_VERSION")}
    real_current = os.environ.pop("PYTEST_CURRENT_TEST", None)
    try:
        assert is_test_process() is False
        assert_not_real_data(REAL_DATA_DIR / "vocab.db", action="open")
    finally:
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value
        if real_current is not None:
            os.environ["PYTEST_CURRENT_TEST"] = real_current


def test_application_engine_is_bound_inside_the_test_data_directory(
    test_data_dir: Path,
) -> None:
    """Evidence, not intent: assert where the app engine actually points."""
    import app.db
    import app.models

    resolved = database_path_from_url(str(app.db.get_engine().url))
    assert resolved is not None
    path = Path(resolved).resolve()
    assert path.is_relative_to(Path(test_data_dir).resolve()), (
        f"application engine is bound to {path}, outside {test_data_dir}"
    )
    assert path != (REAL_DATA_DIR / "vocab.db").resolve()
    assert is_real_data_path(path) is False


def test_app_engine_file_is_not_the_real_database() -> None:
    """Direct string comparison, so a wrong binding can never slip through."""
    import app.db

    resolved = database_path_from_url(str(app.db.get_engine().url))
    assert Path(resolved or "").resolve() != (REAL_DATA_DIR / "vocab.db").resolve()


def test_engine_url_parsing() -> None:
    assert database_path_from_url("sqlite:///D:/tmp/app-data/vocab.db") == (
        "D:/tmp/app-data/vocab.db"
    )
    assert database_path_from_url("sqlite:///:memory:") is None
    assert database_path_from_url("postgresql://x/y") is None


def test_schema_revision_guard_refuses_mismatched_database(tmp_path: Path) -> None:
    """Running code against a database of the wrong revision must not start."""
    from app.db import SchemaRevisionError, code_head_revision, verify_schema_revision

    head = code_head_revision()
    assert head, "the codebase must declare exactly one alembic head"

    database = tmp_path / "app-data" / "vocab.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(database))
    connection.execute("create table alembic_version (version_num varchar(32) not null)")
    connection.execute("insert into alembic_version values ('0001_initial')")
    connection.commit()
    connection.close()

    with pytest.raises(SchemaRevisionError) as error:
        verify_schema_revision(database)
    assert "0001_initial" in str(error.value)

    # The matching revision is accepted.
    verify_schema_revision(database, expected="0001_initial")


def test_schema_revision_guard_refuses_database_without_history(tmp_path: Path) -> None:
    from app.db import SchemaRevisionError, verify_schema_revision

    database = tmp_path / "app-data" / "vocab.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(str(database)).close()

    with pytest.raises(SchemaRevisionError):
        verify_schema_revision(database)
