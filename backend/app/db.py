from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings
from app.testing_guards import assert_not_real_data


class Base(DeclarativeBase):
    pass


def database_path_from_url(url: str) -> str | None:
    """Extract the on-disk file from a SQLAlchemy SQLite URL."""
    if not url.startswith("sqlite"):
        return None
    _, _, tail = url.partition(":///")
    if not tail or tail.startswith(":memory:"):
        return None
    return tail


def make_engine(url: str) -> Engine:
    # Guard the *final resolved* path, not an environment variable that is
    # merely expected to be set.
    database_file = database_path_from_url(url)
    if database_file is not None:
        assert_not_real_data(database_file, action="bind an engine to")
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 5.0}
    engine = create_engine(url, connect_args=connect_args)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            # Without an explicit busy timeout SQLite fails a contended write
            # almost immediately with "database is locked". Two concurrent
            # users are enough to hit this.
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


#: The application engine and session factory, created lazily on first use.
#:
#: Binding them at import time made the database path depend on environment
#: variables that happened to be set when the module was first imported. That
#: indirection is exactly what let a test fixture operate on the real database,
#: so resolution is deferred until an engine is actually needed and the guard in
#: :func:`make_engine` checks the final resolved path every time.
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = make_engine(get_settings().database_url)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


def set_engine(value: Engine) -> None:
    """Point the application at a specific engine (used by the test harness)."""
    global _engine, _session_factory
    _engine = value
    _session_factory = sessionmaker(bind=value, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    with get_session_factory()() as session:
        yield session


class SchemaRevisionError(RuntimeError):
    """Raised when the database revision does not match the code's head."""


def code_head_revision() -> str | None:
    """The single Alembic head revision this codebase expects."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    return heads[0] if len(heads) == 1 else None


def database_revision(database_path: Path) -> str | None:
    """The revision recorded in the database, or None when there is none."""
    import sqlite3

    if not database_path.exists():
        return None
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute("select version_num from alembic_version").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        connection.close()


def verify_schema_revision(database_path: Path, *, expected: str | None = None) -> None:
    """Refuse to start when the database is not at the revision the code needs.

    Running mismatched code against real data is how a working application turns
    into a confusing half-broken one: the app starts, then fails on whichever
    query needs a column that does not exist yet.
    """
    expected = expected or code_head_revision()
    if expected is None:
        return
    actual = database_revision(database_path)
    if actual == expected:
        return
    raise SchemaRevisionError(
        f"数据库版本是 {actual!r}，而当前代码需要 {expected!r}。\n"
        f"数据库：{database_path}\n"
        "请先运行迁移（backend/.venv/Scripts/python.exe -m alembic upgrade head），"
        "或切换到与该数据库版本匹配的代码后再启动。"
    )
