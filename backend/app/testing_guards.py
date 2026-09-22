"""Fail-fast protection for the real user data directory.

The V1.1 database was destroyed because a destructive test fixture ran against
the application's module-level engine, which had resolved to the production
``data/`` directory. The same class of failure exists outside pytest: an
``alembic downgrade`` is destructive by design, and several V1.2 downgrades
delete history. Both paths are protected here.

Rules:

1. :func:`assert_not_real_data` -- the live database is off limits to test
   processes, checked on the *final resolved path*.
2. :func:`assert_safe_for_destructive_operation` -- destructive schema work is
   only allowed inside pytest temporary directories.
3. :func:`is_protected_database` / :func:`assert_downgrade_allowed` -- a
   destructive migration is refused for the live database, backups, recovery
   copies and any other protected artefact, **with or without** an override.

Every failure raises ``UnsafeDatabasePathError`` (or ``SchemaRevisionError`` for
revisions), which is deliberately never caught: the process must die before it
can touch real data.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

#: Environment variables that indicate a test process.
TEST_MODE_ENV_VARS = ("PYTEST_CURRENT_TEST", "PYTEST_VERSION", "VOCAB_TEST_MODE")

#: Path fragments that identify test scratch space.
TEST_PATH_MARKERS = (
    "pytest-tmp",
    "pytest-of-",
    "app-data",
    "test-db",
    "/tmp",
    "/temp",
    "\\temp",
    "-test",
    "test-",
)

#: A database file directly inside one of these directories is real user data.
PRODUCTION_DIR_NAMES = ("data",)

#: Directories inside the project data tree that hold evidence which must never be
#: modified in place: dated backups and the frozen recovery source.
EVIDENCE_DIR_NAMES = ("backups", "backup", "recovery")

#: Directories inside the project data tree whose contents are disposable clones.
DISPOSABLE_DIR_NAMES = ("staging",)

#: Filename suffix used by the backup tooling (``YYYY-MM-DD-vocab.db``,
#: ``manual-...-vocab.db``, ``pre-p1.2-...-vocab.db``).
BACKUP_SUFFIX = "-vocab.db"

#: Environment variable that an operator may set to permit a destructive
#: migration against a disposable clone. It does NOT unlock a protected path.
DESTRUCTIVE_OVERRIDE_ENV = "VOCAB_ALLOW_DESTRUCTIVE_MIGRATION"


class UnsafeDatabasePathError(RuntimeError):
    """Raised when an operation would touch real user data or protected evidence."""


def is_test_process() -> bool:
    """True when this process is a test run."""
    return any(os.environ.get(name) for name in TEST_MODE_ENV_VARS)


def destructive_override_enabled() -> bool:
    return os.environ.get(DESTRUCTIVE_OVERRIDE_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _resolved(path: Path | str) -> Path:
    try:
        return Path(path).expanduser().resolve()
    except OSError:
        return Path(path).expanduser().absolute()


def _real_data_roots() -> set[Path]:
    roots: set[Path] = set()
    declared = os.environ.get("VOCAB_REAL_DATA_DIR")
    if declared:
        roots.add(_resolved(declared))
    # backend/app/testing_guards.py -> project root
    roots.add(Path(__file__).resolve().parents[2] / "data")
    return roots


def is_test_scratch_path(path: Path | str) -> bool:
    """True when ``path`` is clearly inside a test temporary directory."""
    resolved = _resolved(path)
    text = str(resolved).lower().replace("\\", "/")
    if any(marker in text for marker in TEST_PATH_MARKERS):
        return True
    try:
        resolved.relative_to(_resolved(tempfile.gettempdir()))
        return True
    except ValueError:
        return False


def is_real_data_path(path: Path | str) -> bool:
    """True when ``path`` is the project's real ``data/`` database (or a sidecar).

    Test scratch space wins over every other rule: a temporary directory that
    merely happens to be named ``.../app-data/vocab.db`` is not user data.
    """
    resolved = _resolved(path)
    if is_test_scratch_path(resolved):
        return False
    candidates = {resolved}
    for suffix in ("-wal", "-shm", "-journal"):
        if resolved.name.endswith(suffix):
            candidates.add(resolved.with_name(resolved.name[: -len(suffix)]))
    declared = os.environ.get("VOCAB_REAL_DATA_DIR")
    declared_root = _resolved(declared) if declared else None
    for candidate in candidates:
        for root in _real_data_roots():
            if candidate == root or candidate == root / "vocab.db":
                return True
        if (
            candidate.parent.name in PRODUCTION_DIR_NAMES
            and candidate.name.startswith("vocab")
        ):
            return True
        # A declared real data directory is authoritative even for files whose
        # names do not look like a database (such as a recovery copy).
        if declared_root is not None and candidate.is_relative_to(declared_root):
            return True
    return False


def _is_disposable_clone(resolved: Path) -> bool:
    """A staging clone: a real copy of real data that may be recreated at will."""
    candidate = resolved
    for suffix in ("-wal", "-shm", "-journal"):
        if candidate.name.endswith(suffix):
            candidate = candidate.with_name(candidate.name[: -len(suffix)])
    return candidate.parent.name in DISPOSABLE_DIR_NAMES


def _is_evidence_artifact(resolved: Path) -> bool:
    """A backup or recovery copy: real data that must never be altered in place."""
    if resolved.name.endswith(BACKUP_SUFFIX):
        return True
    return resolved.parent.name in EVIDENCE_DIR_NAMES


def is_protected_database(path: Path | str) -> bool:
    """True for anything a destructive migration must never touch.

    Covers the live database, backups, recovery copies, and any other file inside
    the project's data tree or a declared real data directory. This is
    deliberately broader than :func:`is_real_data_path`: that one answers "is this
    the production database", this one answers "may a destructive schema
    operation touch it at all".

    Only two things clear the answer: pytest scratch space, and a staging clone
    (a disposable copy under ``data/staging/``).
    """
    resolved = _resolved(path)
    if _is_disposable_clone(resolved):
        # Explicitly disposable, but still never under a declared real data root
        # when that root *is* the staging directory of a test run.
        declared = os.environ.get("VOCAB_REAL_DATA_DIR")
        return bool(declared) and resolved.is_relative_to(_resolved(declared))
    if is_test_scratch_path(resolved):
        # pytest's temporary directories are disposable by construction, unless
        # the caller declared that very directory as real data.
        roots = {_resolved(root) for root in _real_data_roots()}
        return any(resolved.is_relative_to(root) for root in roots)
    if _is_evidence_artifact(resolved):
        return True
    if resolved.parent.name in PRODUCTION_DIR_NAMES:
        return True
    return any(resolved.is_relative_to(root) for root in _real_data_roots())


def assert_not_real_data(path: Path | str, *, action: str = "open") -> None:
    """Refuse to touch the production database from inside a test process."""
    if not is_test_process():
        return
    if is_real_data_path(path):
        raise UnsafeDatabasePathError(
            f"REFUSING to {action} the real user database at {_resolved(path)}.\n"
            "A test process must never touch data/. This guard exists because that "
            "is exactly how the V1.1 database was destroyed on 2026-09-22.\n"
            "Point the test at a temporary database instead."
        )


def assert_safe_for_destructive_operation(path: Path | str, *, action: str) -> None:
    """Refuse to run schema-destroying work outside a test temporary directory."""
    resolved = _resolved(path)
    assert_not_real_data(resolved, action=action)
    if not is_test_scratch_path(resolved):
        raise UnsafeDatabasePathError(
            f"REFUSING to {action} on {resolved}.\n"
            "Destructive schema operations (drop / downgrade / reset) are only "
            "allowed inside a pytest temporary directory.\n"
            "Mark the path with a test marker (for example a 'pytest-tmp' or "
            "'app-data' directory) or run against a copied database."
        )


def assert_downgrade_allowed(path: Path | str, *, action: str = "downgrade") -> None:
    """Refuse a destructive migration against a protected database.

    The decision is made from the **final resolved database file**, never from an
    environment variable that is merely expected to point at staging.

    An explicit override (``VOCAB_ALLOW_DESTRUCTIVE_MIGRATION=1``) permits a
    destructive migration against a disposable clone, but it deliberately cannot
    unlock the live database or any protected artefact: those are refused with or
    without the override.
    """
    resolved = _resolved(path)

    if is_protected_database(resolved):
        raise UnsafeDatabasePathError(
            f"REFUSING to {action} the protected database {resolved}.\n"
            "This path is the live database, a backup or recovery copy, or another "
            "protected file. Destructive migrations are forbidden there "
            "unconditionally, and an override does not unlock it.\n"
            f"Point the {action} at a staging clone or a pytest temporary database."
        )

    if is_test_scratch_path(resolved) or _is_disposable_clone(resolved):
        return

    if not destructive_override_enabled():
        raise UnsafeDatabasePathError(
            f"REFUSING to {action} {resolved}: the path is not recognised as a "
            "staging clone or a test temporary database.\n"
            f"Set {DESTRUCTIVE_OVERRIDE_ENV}=1 to override, but note that the "
            "override cannot unlock a protected path."
        )
