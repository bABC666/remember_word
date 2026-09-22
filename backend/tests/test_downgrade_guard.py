"""A destructive migration must be refused for real data, before it writes.

``alembic downgrade`` is destructive by design, and several V1.2 downgrades delete
history outright (0005 drops ``user_word_state``, ``user_lexicon`` and
``lexicon_entry``). The guard has to answer one question -- "is this file
disposable?" -- from the **final resolved path**, never from an environment
variable that merely claims "this is staging", and it has to answer it before the
first statement runs.

The strongest available evidence is not the refusal message but the file: after a
refused downgrade the database must be byte-for-byte identical. ``sha256`` is
compared, so not even a WAL frame may have been written.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from app.db import code_head_revision
from app.testing_guards import (
    DESTRUCTIVE_OVERRIDE_ENV,
    UnsafeDatabasePathError,
    assert_downgrade_allowed,
    is_protected_database,
)
from tests.conftest import run_alembic

NOW = "2026-01-01 00:00:00"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(database: Path) -> dict[str, object]:
    """Everything a downgrade could plausibly change."""
    connection = sqlite3.connect(str(database))
    try:
        schema = connection.execute(
            "select type, name, sql from sqlite_master order by type, name"
        ).fetchall()
        tables = [
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table' order by name"
            )
        ]
        revision = connection.execute("select version_num from alembic_version").fetchall()
        counts = {
            table: connection.execute(f'select count(*) from "{table}"').fetchone()[0]
            for table in tables
        }
    finally:
        connection.close()
    return {
        "sha256": digest(database),
        "schema": schema,
        "revision": revision,
        "counts": counts,
    }


def foreign_key_count(database: Path) -> int:
    connection = sqlite3.connect(str(database))
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table' order by name"
            )
        ]
        return sum(
            len(connection.execute(f'pragma foreign_key_list("{table}")').fetchall())
            for table in tables
        )
    finally:
        connection.close()


def scalar(database: Path, statement: str):
    connection = sqlite3.connect(str(database))
    try:
        return connection.execute(statement).fetchone()[0]
    finally:
        connection.close()


# --- classification, with no I/O at all -----------------------------------


def test_the_live_database_is_protected(real_data_dir: Path) -> None:
    assert is_protected_database(real_data_dir / "vocab.db")


@pytest.mark.parametrize(
    "relative",
    [
        "vocab.db",
        "vocab.db-wal",
        "backups/2026-09-22-vocab.db",
        "recovery/vocab-restored-v1.1.db",
        "staging/fresh-clone-0003-to-head.db",
    ],
)
def test_every_file_inside_the_real_data_tree_is_protected(
    real_data_dir: Path, relative: str
) -> None:
    """Including staging: it may be disposable, but it is still not this command's
    business to destroy it implicitly. The override exists for that."""
    assert is_protected_database(real_data_dir / relative), (
        f"{relative} must not be considered disposable"
    )


def test_refusing_the_live_database_is_not_a_warning(real_data_dir: Path) -> None:
    with pytest.raises(UnsafeDatabasePathError):
        assert_downgrade_allowed(real_data_dir / "vocab.db")


def test_the_override_cannot_unlock_a_protected_path(
    real_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The override exists for disposable clones, not for real data."""
    monkeypatch.setenv(DESTRUCTIVE_OVERRIDE_ENV, "1")
    for relative in ("vocab.db", "backups/2026-09-22-vocab.db", "recovery/vocab-restored-v1.1.db"):
        with pytest.raises(UnsafeDatabasePathError):
            assert_downgrade_allowed(real_data_dir / relative)


def test_a_staging_clone_is_allowed(tmp_path: Path) -> None:
    assert_downgrade_allowed(tmp_path / "staging" / "clone.db")


def test_pytest_scratch_space_is_allowed(tmp_path: Path) -> None:
    assert_downgrade_allowed(tmp_path / "app-data" / "scratch.db")


def test_an_unrecognised_path_needs_an_explicit_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path that is neither scratch nor a clone is refused by default.

    The default has to be refusal: "I do not know what this file is" must never
    resolve to "go ahead and drop its tables".
    """
    monkeypatch.delenv(DESTRUCTIVE_OVERRIDE_ENV, raising=False)
    unknown = Path(__file__).resolve().parents[2] / "tool-output" / "results.db"
    assert not is_protected_database(unknown)
    with pytest.raises(UnsafeDatabasePathError) as error:
        assert_downgrade_allowed(unknown)
    assert DESTRUCTIVE_OVERRIDE_ENV in str(error.value)

    monkeypatch.setenv(DESTRUCTIVE_OVERRIDE_ENV, "yes")
    assert_downgrade_allowed(unknown)


# --- end to end: the guard runs before the first statement ----------------


@pytest.fixture()
def pretend_live_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A database inside a directory this run declares to be real user data.

    The file is standalone and disposable, but the *classification* is the one
    that matters: this run declares its directory as real data, so a downgrade
    against it must be refused exactly as it would be for ``data/vocab.db``. Both
    this process and every alembic subprocess get the same declaration, so the
    test asserts the same decision the guard makes.
    """
    database = tmp_path / "app-data" / "data" / "vocab.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOCAB_REAL_DATA_DIR", str(database.parent))

    result = run_alembic(database, "upgrade", "head", extra_env=declared_real(database))
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert is_protected_database(database), "the fixture must set up a protected path"
    return database


def declared_real(database: Path) -> dict[str, str]:
    """The environment that tells alembic this file is real user data."""
    return {"VOCAB_REAL_DATA_DIR": str(database.parent)}


def test_refused_downgrade_leaves_the_file_byte_identical(pretend_live_database: Path) -> None:
    database = pretend_live_database
    before = fingerprint(database)

    result = run_alembic(
        database,
        "downgrade",
        "0006_article_exposure_entry",
        extra_env=declared_real(database),
    )

    assert result.returncode != 0, "the downgrade must not run against real data"
    assert "REFUSING to downgrade the protected database" in result.stderr, result.stderr
    assert UnsafeDatabasePathError.__name__ in result.stderr, result.stderr

    after = fingerprint(database)
    assert after["sha256"] == before["sha256"], "the file was modified"
    assert after == before
    # Not even the schema moved.
    assert after["revision"] == [(code_head_revision(),)]


def test_refused_downgrade_cannot_be_unlocked_by_the_override(
    pretend_live_database: Path,
) -> None:
    database = pretend_live_database
    before = fingerprint(database)

    result = run_alembic(
        database,
        "downgrade",
        "0006_article_exposure_entry",
        extra_env={**declared_real(database), DESTRUCTIVE_OVERRIDE_ENV: "1"},
    )

    assert result.returncode != 0, "the override must not unlock real data"
    assert "REFUSING to downgrade the protected database" in result.stderr, result.stderr
    assert fingerprint(database)["sha256"] == before["sha256"]


def test_staging_clone_downgrade_runs_and_keeps_the_data(tmp_path: Path) -> None:
    """The guard must not be a wall: a disposable clone still downgrades.

    This also exercises ``0007.downgrade()``, which has to rebuild the same nine
    tables to remove the foreign keys, and proves the rebuild preserves rows.
    """
    staging = tmp_path / "app-data" / "staging"
    staging.mkdir(parents=True)
    database = staging / "clone.db"
    elsewhere = tmp_path / "app-data" / "declared-real"
    elsewhere.mkdir()
    env = {"VOCAB_REAL_DATA_DIR": str(elsewhere)}

    result = run_alembic(database, "upgrade", "head", extra_env=env)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert foreign_key_count(database) == 26
    connection = sqlite3.connect(str(database))
    try:
        connection.execute(
            "insert into user (username, display_name, password_hash, role, is_active, "
            "created_at, updated_at) values ('a', 'a', '!', 'admin', 1, ?, ?)",
            (NOW, NOW),
        )
        connection.execute(
            "insert into word (word, phonetic, part_of_speech, source_meanings, source_raw, "
            "anchor, semantic_note, status, first_seen, recall_success, recall_fail, "
            "consecutive_failures, context_exposure, possible_issue, notes, created_at, "
            "updated_at, user_id) values ('keepme', '', '', '[]', 'keepme', '', '', 'new', "
            "?, 0, 0, 0, 0, 0, '', ?, ?, 1)",
            (NOW, NOW, NOW),
        )
        connection.commit()
    finally:
        connection.close()
    before_rows = scalar(database, "select count(*) from word")

    result = run_alembic(database, "downgrade", "0006_article_exposure_entry", extra_env=env)

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert scalar(database, "select version_num from alembic_version") == (
        "0006_article_exposure_entry"
    )
    assert foreign_key_count(database) == 17, "the nine bridge constraints must be gone"
    assert scalar(database, "select count(*) from word") == before_rows
    assert scalar(database, "select word from word where id = 1") == "keepme"
    connection = sqlite3.connect(str(database))
    try:
        assert connection.execute("pragma foreign_key_check").fetchall() == []
        assert connection.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert connection.execute('pragma foreign_key_list("word")').fetchall() == []
    finally:
        connection.close()


def test_upgrade_is_never_treated_as_destructive(pretend_live_database: Path) -> None:
    """Re-running a plain upgrade must stay allowed, whatever the path.

    The guard keys off the command name, so a database has to already be at head
    for this to be a no-op -- which is exactly the normal operator case.
    """
    database = pretend_live_database
    before = fingerprint(database)

    result = run_alembic(database, "upgrade", "head", extra_env=declared_real(database))

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert fingerprint(database)["revision"] == before["revision"]


def test_a_lying_environment_cannot_unlock_a_protected_path(tmp_path: Path) -> None:
    """Variables may say anything; the decision is made from the URL alembic uses.

    Here ``VOCAB_REAL_DATA_DIR`` still declares the real target, so the downgrade
    must be refused no matter what ``VOCAB_DATA_DIR`` claims and even with the
    override set.
    """
    staging = tmp_path / "app-data" / "staging"
    staging.mkdir(parents=True)
    database = staging / "clone.db"
    result = run_alembic(database, "upgrade", "head", extra_env=declared_real(database))
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    before = fingerprint(database)
    result = run_alembic(
        database,
        "downgrade",
        "0006_article_exposure_entry",
        extra_env={
            # The path itself is declared to be real data, so it is protected.
            **declared_real(database),
            # Claims the target is elsewhere and disposable. Neither unlocks it.
            "VOCAB_DATA_DIR": str(tmp_path / "somewhere-else"),
            DESTRUCTIVE_OVERRIDE_ENV: "1",
        },
    )
    assert result.returncode != 0
    assert fingerprint(database)["sha256"] == before["sha256"]


def test_production_paths_are_protected_without_any_environment(monkeypatch) -> None:
    """The project's own data tree is protected even with no variables set.

    The guard must not depend on the operator remembering to export anything.
    """
    monkeypatch.delenv("VOCAB_REAL_DATA_DIR", raising=False)
    project_data = Path(__file__).resolve().parents[2] / "data"
    assert is_protected_database(project_data / "vocab.db")
    assert is_protected_database(project_data / "recovery" / "vocab-restored-v1.1.db")
    with pytest.raises(UnsafeDatabasePathError):
        assert_downgrade_allowed(project_data / "vocab.db")
