from __future__ import annotations

from collections.abc import Generator

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


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
