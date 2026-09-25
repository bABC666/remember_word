from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
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


# --- reporting the same state, for a caller that has to decide ----------------
#
# ``verify_schema_revision`` is a gate: it raises, and the application dies. A
# launcher needs the same facts *before* it decides whether to run a migration, and
# it needs to tell "a fresh install with nothing to lose" apart from "a database
# with real rows that is behind the code". Raising cannot express that difference,
# so the state is reported and the decision is a separate, testable function.


#: The database already matches the code: start, and run no migration at all.
SCHEMA_CURRENT = "current"
#: Nothing is recorded yet (fresh install, or an empty file): ``upgrade head``
#: creates the schema and has nothing to destroy.
SCHEMA_INITIALISE = "initialise"
#: Do not touch the schema. The caller must show the operator how to migrate
#: explicitly, or refuse to start.
SCHEMA_REFUSE = "refuse"


@dataclass(frozen=True)
class MigrationState:
    """What one database looks like relative to the schema this code expects."""

    database_path: Path
    exists: bool
    code_head: str | None
    database_revision: str | None
    #: The revision is an ancestor of the code head, i.e. this database is *behind*
    #: rather than ahead of or unrelated to it.
    is_behind: bool
    table_count: int
    row_count: int
    #: Set when the state could not be established at all.
    problem: str | None = None

    @property
    def is_current(self) -> bool:
        return self.code_head is not None and self.database_revision == self.code_head

    @property
    def has_data(self) -> bool:
        return self.row_count > 0

    def as_dict(self) -> dict[str, object]:
        return {
            "database": str(self.database_path),
            "exists": self.exists,
            "code_head": self.code_head,
            "database_revision": self.database_revision,
            "is_current": self.is_current,
            "is_behind": self.is_behind,
            "has_data": self.has_data,
            "table_count": self.table_count,
            "row_count": self.row_count,
            "problem": self.problem,
        }


def _head_chain(head: str) -> set[str]:
    """Every revision from the base up to ``head``, inclusive.

    Used to tell "behind" from "ahead of or unrelated to": only a revision in this
    chain can be brought forward by ``upgrade head``, and a database at any other
    revision must not be silently migrated.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    script = ScriptDirectory.from_config(config)
    return {revision.revision for revision in script.walk_revisions("base", head)}


def _row_and_table_counts(connection: object, tables: list[str]) -> tuple[int, int]:
    total = 0
    for table in tables:
        total += int(connection.execute(f'select count(*) from "{table}"').fetchone()[0])
    return len(tables), total


def migration_state(database_path: Path) -> MigrationState:
    """Report the schema state read-only. Never writes, never raises for state.

    A missing file, an unreadable one, a version table with the wrong number of
    rows and two code heads are all *answers* here rather than exceptions: the
    caller has to be able to say "I do not know, so I will not migrate" instead of
    crashing before it can explain itself.
    """
    import sqlite3

    path = Path(database_path)
    # The code head does not depend on the database: report it even when the file
    # is missing, because "we cannot tell what this code expects" is a different
    # problem from "the database is not there yet" and the caller has to act
    # differently on each.
    try:
        head: str | None = code_head_revision()
    except SchemaRevisionError as error:
        return MigrationState(
            database_path=path, exists=path.exists(), code_head=None,
            database_revision=None, is_behind=False, table_count=0, row_count=0,
            problem=str(error),
        )

    if not path.exists():
        return MigrationState(
            database_path=path, exists=False, code_head=head, database_revision=None,
            is_behind=False, table_count=0, row_count=0,
        )

    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        return MigrationState(
            database_path=path, exists=True, code_head=head, database_revision=None,
            is_behind=False, table_count=0, row_count=0,
            problem=f"无法以只读方式打开数据库：{error}",
        )

    try:
        tables = [
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table' "
                "and name not like 'sqlite_%' and name <> 'alembic_version' "
                "order by name"
            )
        ]
        table_count, row_count = _row_and_table_counts(connection, tables)
        try:
            rows = connection.execute("select version_num from alembic_version").fetchall()
        except sqlite3.Error:
            # No version table. An empty file is a fresh install; a file that has
            # tables but no version marker is something this code cannot reason
            # about, and guessing would be the wrong way to find out.
            return MigrationState(
                database_path=path, exists=True, code_head=head, database_revision=None,
                is_behind=False, table_count=table_count, row_count=row_count,
            )
        revisions = [row[0] for row in rows]
        if len(revisions) != 1 or not isinstance(revisions[0], str) or not revisions[0]:
            return MigrationState(
                database_path=path, exists=True, code_head=head, database_revision=None,
                is_behind=False, table_count=table_count, row_count=row_count,
                problem=(
                    f"alembic_version 表有 {len(revisions)} 行（期望恰好 1 行且非空）："
                    f"{revisions}"
                ),
            )
    finally:
        connection.close()

    try:
        is_behind = revisions[0] != head and revisions[0] in _head_chain(head)
    except Exception as error:  # noqa: BLE001 -- an unreadable graph is not a decision
        return MigrationState(
            database_path=path, exists=True, code_head=head, database_revision=revisions[0],
            is_behind=False, table_count=table_count, row_count=row_count,
            problem=f"无法读取 migration 图以判断先后关系：{error}",
        )
    return MigrationState(
        database_path=path, exists=True, code_head=head, database_revision=revisions[0],
        is_behind=is_behind, table_count=table_count, row_count=row_count,
    )


def schema_action(state: MigrationState) -> tuple[str, str]:
    """What a launcher should do about the schema, and why, in one place.

    The rule this encodes, and the reason it is not "just run ``upgrade head``":

    * a database that already matches starts, and nothing touches the schema;
    * a database with nothing recorded is brought up to head -- there is no content
      to lose and the server cannot start without a schema;
    * a database that **is behind the code and has rows** is never migrated
      automatically. That state means someone's real data is about to be changed by
      a step that has had no rehearsal, no backup and no operator watching, and the
      answer is to tell them how to do it deliberately;
    * anything unreadable, corrupt, ahead of the code, or otherwise not understood
      is refused for the same reason -- not knowing is not permission.
    """
    if state.problem:
        return SCHEMA_REFUSE, state.problem
    if not state.exists:
        return SCHEMA_INITIALISE, "数据库尚不存在：全新安装，将创建 schema。"
    if state.is_current:
        return SCHEMA_CURRENT, f"数据库已是代码所需的 revision（{state.code_head}）。"
    if state.database_revision is None and state.table_count == 0:
        return SCHEMA_INITIALISE, "数据库文件为空：将创建 schema。"
    if state.database_revision is None:
        return SCHEMA_REFUSE, (
            f"数据库有 {state.table_count} 张表但没有 alembic_version 记录，"
            "无法判断它处于哪个 revision；拒绝自动迁移。"
        )
    if not state.is_behind:
        return SCHEMA_REFUSE, (
            f"数据库 revision {state.database_revision!r} 不是当前代码 head "
            f"{state.code_head!r} 的祖先（可能数据库更新、或 revision 不属于这条链）；"
            "拒绝自动迁移。"
        )
    if state.has_data:
        return SCHEMA_REFUSE, (
            f"数据库落后代码：{state.database_revision} -> {state.code_head}，"
            f"且库中已有 {state.row_count} 行数据（{state.table_count} 张表）。"
        )
    return SCHEMA_INITIALISE, (
        f"数据库落后代码（{state.database_revision} -> {state.code_head}）但没有任何数据行，"
        "将直接升级。"
    )
