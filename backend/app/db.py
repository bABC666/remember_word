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
    """Raised when the database revision does not match the code's head.

    Every abnormal state raises this: the application must never start on a
    guess. A warning would let a mismatched process run against real data, which
    is exactly the failure this guard exists to prevent.
    """


def code_head_revisions() -> list[str]:
    """Every Alembic head this codebase declares.

    Normally exactly one. More than one means the migration graph has branched,
    which is a defect in the repository, not something to paper over.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    try:
        return sorted(ScriptDirectory.from_config(config).get_heads())
    except Exception as error:
        raise SchemaRevisionError(
            f"无法读取 migration 目录以确定代码 head：{error}"
        ) from error


def code_head_revision() -> str:
    """The single head revision this codebase expects.

    Raises rather than returning ``None`` so a caller can never mistake "cannot
    tell" for "nothing to check". An empty or branched head set is fatal.
    """
    heads = code_head_revisions()
    if len(heads) != 1:
        raise SchemaRevisionError(
            f"代码的 migration head 数量为 {len(heads)}（期望恰好 1 个）：{heads}。\n"
            "多个 head 说明 migration 图出现分叉，必须先修复 migration 历史。"
        )
    return heads[0]


def database_revisions(database_path: Path) -> list[str]:
    """Every row of ``alembic_version`` in the database.

    Every row is counted, including ones that are not usable strings. Alembic
    records exactly one non-null row, so a second row -- or a row that is NULL or
    empty -- means the version table is corrupt and the schema state is unknowable.
    Dropping unusable rows and then finding "exactly one left" would turn a
    corrupt table into a passing check, which is the opposite of this guard's job.
    """
    import sqlite3

    if not database_path.exists():
        raise SchemaRevisionError(f"数据库文件不存在：{database_path}")

    try:
        connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise SchemaRevisionError(
            f"无法以只读方式打开 {database_path}：{error}"
        ) from error
    try:
        try:
            rows = connection.execute("select version_num from alembic_version").fetchall()
        except sqlite3.Error as error:
            raise SchemaRevisionError(
                f"无法读取 {database_path} 的 alembic_version 表：{error}\n"
                "该数据库可能不是由 Alembic 管理的。"
            ) from error
        revisions = [row[0] for row in rows]
        if len(revisions) != 1 or not isinstance(revisions[0], str) or not revisions[0]:
            raise SchemaRevisionError(
                f"{database_path} 的 alembic_version 表有 {len(revisions)} 行"
                f"（期望恰好 1 行且非空）：{revisions}"
            )
        return revisions
    finally:
        connection.close()


def database_revision(database_path: Path) -> str:
    """The single revision recorded in the database, or raise."""
    return database_revisions(database_path)[0]


def verify_schema_revision(database_path: Path, *, expected: str | None = None) -> None:
    """Refuse to start unless the schema state is unambiguous and matching.

    All three conditions must hold:

    1. the codebase declares exactly one Alembic head;
    2. the database records exactly one revision row;
    3. the two are equal.

    Anything else -- zero or multiple code heads, zero or multiple database
    revisions, an unreadable or missing version table, or a mismatch -- raises
    ``SchemaRevisionError``. There is no warning path and no implicit default.
    """
    head = expected or code_head_revision()
    actual = database_revision(database_path)
    if actual != head:
        raise SchemaRevisionError(
            f"数据库版本是 {actual!r}，而当前代码需要 {head!r}。\n"
            f"数据库：{database_path}\n"
            "请先运行迁移（backend/.venv/Scripts/python.exe -m alembic upgrade head），"
            "或切换到与该数据库版本匹配的代码后再启动。"
        )
