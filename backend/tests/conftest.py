"""Shared test fixtures with hard isolation from the real user data directory.

History: on 2026-09-22 the production database was destroyed because a test
fixture ran ``Base.metadata.drop_all(engine)`` against the application's
module-level engine, which had resolved to the real ``data/`` directory. These
fixtures remove that whole class of failure:

* settings are resolved for a temporary data directory *before* ``app.*`` is
  imported, and the guard in ``app.config`` fails fast if that ever regresses;
* application tables are never built on the module-level engine: every test that
  needs a database gets its own temporary engine and Session factory;
* ``VOCAB_REAL_DATA_DIR`` is pinned to the real ``data/`` directory so the guard
  knows exactly what to refuse, and every child process inherits a temporary
  ``VOCAB_DATA_DIR``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# --- isolation must happen before any ``app.*`` import --------------------
_SESSION_DATA_DIR = Path(tempfile.mkdtemp(prefix="shici-tests-app-data-"))
os.environ["VOCAB_DATA_DIR"] = str(_SESSION_DATA_DIR)
#: Tells the application guard which directory counts as "real user data".
os.environ["VOCAB_REAL_DATA_DIR"] = str(Path(__file__).resolve().parents[2] / "data")


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    return _SESSION_DATA_DIR


@pytest.fixture(scope="session", autouse=True)
def real_data_untouched() -> None:
    """Fail immediately if the application engine is bound to the real database.

    The schema for that engine is then created inside the temporary session data
    directory (additively only: nothing is ever dropped), so tests that exercise
    the FastAPI application through its own engine still work.
    """
    import app.db
    import app.models
    from app.db import Base
    from app.testing_guards import (
        assert_not_real_data,
        assert_safe_for_destructive_operation,
    )

    resolved = app.db.database_path_from_url(str(app.db.engine.url))
    assert resolved, "test session must use an on-disk SQLite database"
    assert_not_real_data(resolved, action="bind the application engine to")
    assert Path(resolved).resolve().is_relative_to(_SESSION_DATA_DIR.resolve()), (
        f"the application engine is bound to {resolved}, which is outside the "
        f"test data directory {_SESSION_DATA_DIR}"
    )

    # Additive schema build for the application engine. Destructive operations
    # remain forbidden: drop_all in a fixture is what destroyed production data.
    assert_safe_for_destructive_operation(
        _SESSION_DATA_DIR / "vocab.db", action="build the application test schema in"
    )
    Base.metadata.create_all(app.db.engine)
    yield


@pytest.fixture()
def session(tmp_path: Path):
    """A fresh temporary database with the full current schema.

    The schema is built on a private engine, never on the application's
    module-level engine.
    """
    from sqlalchemy.orm import sessionmaker

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
    """A temporary database for tests that exercise the FastAPI application.

    Pointing the application at it means tests never rely on the application's
    import-time engine having been redirected.
    """
    from sqlalchemy.orm import sessionmaker

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
