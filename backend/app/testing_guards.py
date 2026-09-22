"""Fail-fast protection for the real user data directory.

The V1.1 database was destroyed because a destructive test fixture ran against
the application's module-level engine, which had resolved to the production
``data/`` directory. Relying on ``VOCAB_DATA_DIR`` "probably being set" was not
enough, so every path that can bind an engine, create, drop or migrate a schema
now passes through this module first.

Two rules:

1. :func:`assert_not_real_data` refuses to bind an engine to, or resolve
   settings for, the real production database while a test session is running.
2. :func:`assert_safe_for_destructive_operation` refuses to run schema
   destroying work on anything that is not clearly inside a test temporary
   directory.

Both raise :class:`UnsafeDatabasePathError`, which is deliberately never caught:
the process must die before it can touch real data.
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


class UnsafeDatabasePathError(RuntimeError):
    """Raised when an operation would touch real user data from a test."""


def is_test_process() -> bool:
    """True when this process is a test run."""
    return any(os.environ.get(name) for name in TEST_MODE_ENV_VARS)


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
