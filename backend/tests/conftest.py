"""Shared test fixtures with hard isolation from the real user data directory.

History: on 2026-09-22 the production database was destroyed because a test
fixture ran ``Base.metadata.drop_all(engine)`` against the application's
module-level engine, which had resolved to the real ``data/`` directory. These
fixtures remove that whole class of failure:

* settings resolve to a temporary data directory *before* ``app.*`` is imported,
  and the guard in ``app.config`` fails fast if that ever regresses;
* application tables are never built on the module-level engine: every test that
  needs a database gets its own temporary engine and Session factory;
* ``VOCAB_REAL_DATA_DIR`` is pinned to the real ``data/`` directory so the guard
  knows exactly what to refuse;
* the session temporary database is built by running the real Alembic
  migrations, so tests exercise the schema users actually get.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

# --- isolation must happen before any ``app.*`` import --------------------
_SESSION_DATA_DIR = Path(tempfile.mkdtemp(prefix="app-data-pytest-"))
_REAL_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
os.environ["VOCAB_DATA_DIR"] = str(_SESSION_DATA_DIR)
#: Tells the application guard which directory counts as "real user data".
os.environ["VOCAB_REAL_DATA_DIR"] = str(_REAL_DATA_DIR)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    return _SESSION_DATA_DIR


@pytest.fixture(scope="session")
def real_data_dir() -> Path:
    return _REAL_DATA_DIR


@pytest.fixture(scope="session", autouse=True)
def isolated_application_engine() -> None:
    """Bind the application to a migrated temporary database and prove it.

    No ``drop_all`` runs anywhere in this fixture. Dropping every table on a
    shared engine is exactly the operation that destroyed production data.
    """
    import app.db
    import app.models
    from app.testing_guards import (
        assert_not_real_data,
        assert_safe_for_destructive_operation,
    )

    database = _SESSION_DATA_DIR / "vocab.db"
    assert_not_real_data(database, action="point the application engine at")
    assert_safe_for_destructive_operation(
        database, action="build the application test schema in"
    )

    env = dict(os.environ)
    env["VOCAB_DATA_DIR"] = str(_SESSION_DATA_DIR)
    env["VOCAB_REAL_DATA_DIR"] = str(_SESSION_DATA_DIR)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(BACKEND_ROOT / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"could not build the test schema with alembic:\n{result.stdout}\n{result.stderr}"
        )

    app.db.set_engine(app.db.make_engine(f"sqlite:///{database.as_posix()}"))

    # Evidence, not intent: assert where the application engine actually points.
    resolved = app.db.database_path_from_url(str(app.db.get_engine().url))
    assert resolved, "test session must use an on-disk SQLite database"
    assert Path(resolved).resolve() == database.resolve(), (
        f"application engine points at {resolved}, expected {database}"
    )
    assert_not_real_data(resolved, action="bind the application engine to")
    assert Path(resolved).resolve().is_relative_to(_SESSION_DATA_DIR.resolve())
    yield


@pytest.fixture(autouse=True)
def pin_data_dir(isolated_application_engine) -> None:
    """Re-assert the temporary data directory before every test.

    Several tests deliberately manipulate ``VOCAB_DATA_DIR`` (and clear the
    settings cache) to exercise configuration handling. Those changes must never
    leak into the next test, so both the environment and the cached settings are
    reset here. Without this, the next test can resolve settings against another
    test's scratch directory.
    """
    from app.config import get_settings

    def _pin() -> None:
        os.environ["VOCAB_DATA_DIR"] = str(_SESSION_DATA_DIR)
        os.environ["VOCAB_REAL_DATA_DIR"] = str(_REAL_DATA_DIR)
        get_settings.cache_clear()

    _pin()
    yield
    _pin()


@pytest.fixture()
def session(tmp_path: Path):
    """A fresh temporary database with the full current schema."""
    import app.models  # noqa: F401
    from app.db import Base, make_engine
    from app.testing_guards import assert_safe_for_destructive_operation

    database = tmp_path / "session-test-db.sqlite"
    assert_safe_for_destructive_operation(database, action="build a test schema in")
    engine = make_engine(f"sqlite:///{database.as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as value:
        yield value
    engine.dispose()


@pytest.fixture()
def app_session(tmp_path: Path, real_data_untouched):
    """A temporary database for tests that exercise the FastAPI application."""
    import app.models  # noqa: F401
    from app.db import Base, make_engine
    from app.testing_guards import assert_safe_for_destructive_operation

    database = tmp_path / "app-test-db.sqlite"
    assert_safe_for_destructive_operation(database, action="build an app test schema in")
    engine = make_engine(f"sqlite:///{database.as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as value:
        yield value
    engine.dispose()
