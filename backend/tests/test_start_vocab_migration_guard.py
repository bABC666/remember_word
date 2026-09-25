"""Tests for the schema decision the launcher makes before starting the server.

``scripts/start-vocab.ps1`` used to run ``alembic upgrade head`` unconditionally, and
the desktop shortcut runs that launcher. A double-click after deploying a release with
a new migration therefore applied it to the user's real database with none of the four
gates -- no rehearsal, no backup, no explicit revision, no verification -- and the
server then started cleanly, so nothing reported that it had happened.

``scripts/prepare-database.ps1`` now owns that decision, and these tests pin the three
states an operator actually meets:

1. **the database matches the code** -- start, and do not touch the schema at all;
2. **the database is behind the code and has rows** -- refuse, print the explicit
   migration procedure, and leave the database exactly as it was;
3. **a previous migration attempt failed** -- refuse again rather than retrying.

The third one is the dangerous one. SQLite runs DDL non-transactionally, so a
migration that failed part-way can leave tables created and the revision unadvanced;
a launcher that retries ``upgrade head`` meets "table already exists" and leaves the
operator with a database that will not start and no backup to fall back to. These
tests build exactly that state and require the launcher to stay out of it.

Also covered: a fresh install still initialises, because refusing there would leave
the product unable to start for the first time.

Every test points the script at a throwaway database under pytest's temporary
directory. The real ``data/vocab.db`` is never opened, and no test migrates it.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREPARE_SCRIPT = PROJECT_ROOT / "scripts" / "prepare-database.ps1"
START_SCRIPT = PROJECT_ROOT / "scripts" / "start-vocab.ps1"
PYTHON = PROJECT_ROOT / "backend" / ".venv" / "Scripts" / "python.exe"

WINDOWS_POWERSHELL = (
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)


def find_powershell() -> str | None:
    for name in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
        found = shutil.which(name)
        if found:
            return found
    if WINDOWS_POWERSHELL.exists():
        return str(WINDOWS_POWERSHELL)
    return None


POWERSHELL = find_powershell()

pytestmark = [
    pytest.mark.skipif(POWERSHELL is None, reason="no PowerShell host is available"),
    pytest.mark.skipif(
        not PYTHON.exists(),
        reason="backend virtualenv is required to inspect a database revision",
    ),
]


def run_prepare(database: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run the guard against one throwaway database."""
    child_env = dict(os.environ)
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    # Point anything the child might resolve at the temporary directory, so a
    # mistake in path handling cannot reach the real data directory.
    child_env["VOCAB_DATA_DIR"] = str(database.parent)
    child_env["VOCAB_REAL_DATA_DIR"] = str(PROJECT_ROOT / "data")
    if env:
        child_env.update(env)
    return subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(PREPARE_SCRIPT),
            "-DatabasePath", str(database),
            "-PythonPath", str(PYTHON),
            "-RepositoryRoot", str(PROJECT_ROOT),
        ],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=child_env,
    )


def code_head() -> str:
    result = subprocess.run(
        [str(PYTHON), "-c",
         "from app.db import code_head_revision; print(code_head_revision())"],
        cwd=str(PROJECT_ROOT / "backend"),
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    return result.stdout.strip()


#: Reads the revision directly before the code head out of the migration graph.
BEHIND_HEAD_SNIPPET = """\
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.db import code_head_revision

config = Config('alembic.ini')
config.set_main_option('script_location', 'alembic')
head = code_head_revision()
print(ScriptDirectory.from_config(config).get_revision(head).down_revision)
"""


def behind_head() -> str:
    """The revision directly before the code head, read from the migration graph.

    Derived rather than hardcoded: the whole point of the "behind the code" tests is
    that they exercise a real *older* schema, and a pinned revision would silently
    become the head the first time someone added a migration.
    """
    result = subprocess.run(
        [str(PYTHON), "-c", BEHIND_HEAD_SNIPPET],
        cwd=str(PROJECT_ROOT / "backend"),
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    previous = result.stdout.strip()
    assert previous and previous != "None", "the migration chain needs a parent of head"
    return previous


def build_database(path: Path, *, revision: str, with_rows: bool = True) -> None:
    """A real database at ``revision``, optionally carrying rows.

    Migrations are run to the requested revision so the schema is genuine rather than
    a hand-written approximation; ``with_rows`` then decides whether the database
    looks like a deployment or an empty scratch file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{path.resolve().as_posix()}"
    env = dict(os.environ)
    env["VOCAB_DATA_DIR"] = str(path.parent)
    env["VOCAB_REAL_DATA_DIR"] = str(path.parent)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        [str(PYTHON), "-m", "alembic", "-c", "alembic.ini", "-x", f"db_url={url}",
         "upgrade", revision],
        cwd=str(PROJECT_ROOT / "backend"),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, env=env,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    if with_rows:
        connection = sqlite3.connect(str(path))
        try:
            connection.execute(
                "insert into app_setting (key, value, updated_at) values (?, ?, ?)",
                ("p29-guard-probe", "kept", "2026-01-01 00:00:00"),
            )
            connection.commit()
        finally:
            connection.close()


def revision_of(path: Path) -> str:
    connection = sqlite3.connect(str(path))
    try:
        return connection.execute("select version_num from alembic_version").fetchone()[0]
    finally:
        connection.close()


def table_names(path: Path) -> set[str]:
    connection = sqlite3.connect(str(path))
    try:
        return {
            row[0] for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        }
    finally:
        connection.close()


def guard_actions(tmp_path: Path, *databases: Path) -> list[str]:
    """The action ``migration-status`` reports for each database, in order."""
    actions = []
    for database in databases:
        result = subprocess.run(
            [str(PYTHON), "-m", "app.cli", "migration-status", "--database", str(database)],
            cwd=str(PROJECT_ROOT / "backend"),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        actions.append(json.loads(result.stdout)["action"])
    return actions


# --- 1. the database already matches the code --------------------------------


def test_a_database_at_the_code_head_starts_without_touching_the_schema(
    tmp_path: Path,
) -> None:
    database = tmp_path / "at-head.db"
    build_database(database, revision=code_head())
    before_revision = revision_of(database)
    before_tables = table_names(database)
    before_bytes = database.read_bytes()

    result = run_prepare(database)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "no migration needed" in result.stdout
    # Nothing ran: same revision, same tables, and the file is byte-identical.
    assert revision_of(database) == before_revision
    assert table_names(database) == before_tables
    assert database.read_bytes() == before_bytes


# --- 2. behind the code, with rows -------------------------------------------


def test_a_database_behind_the_code_with_rows_is_refused(tmp_path: Path) -> None:
    head = code_head()
    behind = behind_head()
    assert behind != head

    database = tmp_path / "behind.db"
    build_database(database, revision=behind, with_rows=True)
    before_revision = revision_of(database)
    before_bytes = database.read_bytes()

    result = run_prepare(database)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "Refusing to migrate automatically" in result.stdout
    # The guidance has to be actionable without reading the source.
    assert "alembic" in result.stdout
    assert "upgrade" in result.stdout
    assert "verified backup" in result.stdout
    assert "0007-production-migration-runbook" in result.stdout
    assert "upgrade head" in result.stdout  # named as the thing not to do on real data
    # And the database is exactly as it was.
    assert revision_of(database) == before_revision
    assert database.read_bytes() == before_bytes


def test_the_refusal_names_both_revisions_and_the_likely_cost(tmp_path: Path) -> None:
    head = code_head()
    behind = behind_head()

    database = tmp_path / "named.db"
    build_database(database, revision=behind, with_rows=True)

    result = run_prepare(database)

    assert result.returncode == 3
    assert behind in result.stdout
    assert head in result.stdout
    assert "Tables/rows" in result.stdout


# --- 3. a previous migration attempt failed ----------------------------------


def test_a_launcher_restart_after_a_failed_migration_does_not_retry(
    tmp_path: Path,
) -> None:
    """The state a non-transactional DDL failure leaves behind.

    Some tables from the newer revision exist, the revision marker never advanced,
    and the schema is therefore inconsistent. Retrying ``upgrade head`` would meet
    "table already exists" and cannot help; the launcher must refuse and hand the
    database to an operator, with everything still in place for a diagnosis.
    """
    behind = behind_head()

    database = tmp_path / "half-migrated.db"
    build_database(database, revision=behind, with_rows=True)
    # Emulate the partial DDL while keeping the database a genuine `behind` schema.
    connection = sqlite3.connect(str(database))
    try:
        connection.execute("create table phase29_partial_leftover (id integer primary key)")
        connection.execute(
            "insert into phase29_partial_leftover (id) values (1)"
        )
        connection.commit()
    finally:
        connection.close()
    before_bytes = database.read_bytes()

    result = run_prepare(database)

    assert result.returncode == 3, result.stdout + result.stderr
    assert revision_of(database) == behind
    # The incomplete work is preserved rather than half-undone or duplicated.
    assert "phase29_partial_leftover" in table_names(database)
    assert database.read_bytes() == before_bytes
    assert "Refusing to migrate automatically" in result.stdout


def test_an_unreadable_version_table_is_refused_rather_than_guessed(
    tmp_path: Path,
) -> None:
    """Not knowing is not permission.

    A database with tables but no usable revision marker is not something this code
    can reason about. Treating it as "empty, so just migrate" would run a migration
    against a schema nobody has identified.
    """
    database = tmp_path / "corrupt-version.db"
    build_database(database, revision=code_head(), with_rows=True)
    connection = sqlite3.connect(str(database))
    try:
        connection.execute("delete from alembic_version")
        connection.commit()
    finally:
        connection.close()
    before_tables = table_names(database)

    result = run_prepare(database)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "Refusing to migrate automatically" in result.stdout
    # It did not "helpfully" create a schema on top of the unknown one.
    assert table_names(database) == before_tables
    assert "alembic_version" in before_tables


# --- 4. a fresh install still works ------------------------------------------


def test_a_missing_database_is_initialised(tmp_path: Path) -> None:
    database = tmp_path / "fresh" / "vocab.db"
    assert not database.exists()

    result = run_prepare(database)

    assert result.returncode == 0, result.stdout + result.stderr
    assert database.exists()
    assert revision_of(database) == code_head()
    assert "initialis" in result.stdout.lower()


def test_an_empty_database_file_is_initialised(tmp_path: Path) -> None:
    database = tmp_path / "empty.db"
    database.write_bytes(b"")

    result = run_prepare(database)

    assert result.returncode == 0, result.stdout + result.stderr
    assert revision_of(database) == code_head()


def test_a_database_with_tables_but_no_version_table_is_refused(
    tmp_path: Path,
) -> None:
    """A different unknown state from a corrupt version table.

    Here there is no ``alembic_version`` table at all, so there is no "problem" to
    report -- just an absence. This is the case only the "no revision recorded"
    rule catches, which is why it gets its own test rather than riding on the
    corrupt-table one.
    """
    database = tmp_path / "no-version-table.db"
    build_database(database, revision=code_head(), with_rows=True)
    connection = sqlite3.connect(str(database))
    try:
        connection.execute("drop table alembic_version")
        connection.commit()
    finally:
        connection.close()
    before_tables = table_names(database)

    result = run_prepare(database)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "Refusing to migrate automatically" in result.stdout
    assert table_names(database) == before_tables


# --- 4. the decision function, rule by rule ----------------------------------
#
# The integration tests above assert outcomes, and for several inputs the outcome
# is deliberately over-determined: a corrupt version table is refused by three
# independent rules, so breaking one of them does not change what an operator sees.
# That is good defence in depth but it makes those tests unable to localise a
# regression, so each rule is pinned here against the state it exists for -- using
# the reason text, which is what tells an operator which rule fired.


def _state(**overrides: object):
    from app.db import MigrationState

    base: dict[str, object] = {
        "database_path": Path("probe.db"),
        "exists": True,
        "code_head": "0007_head",
        "database_revision": "0007_head",
        "is_behind": False,
        "table_count": 3,
        "row_count": 0,
        "problem": None,
    }
    base.update(overrides)
    return MigrationState(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(("state", "expected", "fragment"), [
    # Matching the code is the only state that does not touch the schema.
    (_state(), "current", "已是代码所需的 revision"),
    # Nothing there yet: safe to create.
    (_state(exists=False, database_revision=None, table_count=0),
     "initialise", "不存在"),
    (_state(database_revision=None, table_count=0, row_count=0),
     "initialise", "空"),
    # An empty database that is behind is still safe to bring forward.
    (_state(database_revision="0006_old", is_behind=True, row_count=0),
     "initialise", "没有任何数据行"),
    # The rule this whole change exists for.
    (_state(database_revision="0006_old", is_behind=True, row_count=42),
     "refuse", "行数据"),
    # A reported problem always wins, whatever else the state looks like.
    (_state(problem="version table is corrupt"),
     "refuse", "version table is corrupt"),
    # Tables but no recorded revision: absence, not a problem.
    (_state(database_revision=None, table_count=17, row_count=5),
     "refuse", "alembic_version"),
    # Not an ancestor of head: ahead of the code, or unrelated to it.
    (_state(database_revision="9999_from_the_future", is_behind=False, row_count=5),
     "refuse", "祖先"),
])
def test_schema_action_decides_each_state_for_its_own_reason(
    state, expected: str, fragment: str
) -> None:
    from app.db import schema_action

    action, reason = schema_action(state)

    assert action == expected
    assert fragment in reason, f"{action} / {reason}"


def test_the_scaffolding_states_above_are_valid() -> None:
    """Fail loudly if the parametrisation stops matching the real dataclass."""
    assert _state().is_current is True
    assert _state(row_count=42).has_data is True
    assert _state(row_count=0).has_data is False


# --- 5. the launcher itself --------------------------------------------------


def test_the_launcher_no_longer_migrates_on_its_own() -> None:
    """The hazard was the launcher's own `upgrade head`; it must be gone.

    Comments are excluded: the launcher explains *why* it does not migrate, and that
    explanation necessarily names the thing it no longer does. What matters is that
    no executable line reaches alembic.
    """
    text = START_SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )

    assert "alembic" not in code
    # `pip install --upgrade pip` is fine; an alembic upgrade is not.
    assert "upgrade head" not in code
    assert "'upgrade'" not in code
    assert "prepare-database.ps1" in code
    # And it must stop rather than start the server when the guard refuses.
    assert "exit $LASTEXITCODE" in code


def test_the_guard_is_the_only_place_that_migrates() -> None:
    script = PREPARE_SCRIPT.read_text(encoding="utf-8")

    # Exactly one upgrade, and it is reached only on the initialise path, which by
    # definition is a database with no rows to lose.
    assert script.count("'upgrade'") == 1
    assert script.count("'head'") == 1
    assert "migration-status" in script


def test_the_operator_scripts_stay_ascii() -> None:
    """Windows PowerShell reads a BOM-less script as ANSI.

    Non-ASCII text in these files is mis-decoded before parsing, which produced a
    real "Missing closing '}'" parse error while this guard was being written. The
    operator-facing scripts are English for that reason, and the Python CLI they
    call carries the Chinese messages instead.
    """
    for script in (PREPARE_SCRIPT, START_SCRIPT):
        text = script.read_text(encoding="utf-8")
        offenders = sorted({char for char in text if ord(char) > 127})
        assert not offenders, f"{script.name} contains non-ASCII: {offenders}"


def test_the_status_command_is_read_only_and_reports_the_same_facts(
    tmp_path: Path,
) -> None:
    """The guard is only as good as what it is told, so pin the report itself."""
    head = code_head()
    at_head = tmp_path / "report-at-head.db"
    build_database(at_head, revision=head)
    behind = tmp_path / "report-behind.db"
    build_database(behind, revision=behind_head(), with_rows=True)
    fresh = tmp_path / "report-fresh.db"

    actions = guard_actions(tmp_path, at_head, behind, fresh)

    assert actions == ["current", "refuse", "initialise"]
    # Read-only: the inspection left both databases byte-identical.
    assert revision_of(at_head) == head
    assert revision_of(behind) == behind_head()
    assert not fresh.exists()


def test_the_behind_helper_really_is_behind() -> None:
    """Guard against the "behind" tests passing for the wrong reason.

    If ``behind_head()`` ever returned the head, those tests would silently be
    exercising the "current" path and prove nothing.
    """
    assert behind_head() != code_head()
