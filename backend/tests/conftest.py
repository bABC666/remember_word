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
from datetime import UTC, datetime
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

# Argon2id is deliberately expensive. Real logins pay that cost once; the test
# suite would hash on every user creation, so use the cheapest valid parameters.
# Production keeps pwdlib's recommended defaults.
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

import app.security

app.security._PASSWORD_HASH = PasswordHash(
    [Argon2Hasher(time_cost=1, memory_cost=8, parallelism=1, hash_len=32, salt_len=8)]
)


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    return _SESSION_DATA_DIR


@pytest.fixture(scope="session")
def real_data_dir() -> Path:
    return _REAL_DATA_DIR


def run_alembic(
    database: Path,
    *arguments: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run alembic against an explicit database file.

    The URL is always absolute and always passed with ``-x db_url``: a relative
    ``sqlite:///`` URL is resolved against the child's working directory, which
    once silently pointed a verification run at the wrong file.

    ``PYTHONIOENCODING`` is pinned so the child's messages decode identically on
    every console code page; assertion messages contain Chinese, and a cp936
    round-trip through a pipe is not reliable.
    """
    resolved = Path(database).resolve()
    env = dict(os.environ)
    root = resolved.parent if resolved.parent.name else resolved
    env["VOCAB_DATA_DIR"] = str(root)
    env["VOCAB_REAL_DATA_DIR"] = str(root)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(BACKEND_ROOT / "alembic.ini"),
            "-x",
            f"db_url=sqlite:///{resolved.as_posix()}",
            *arguments,
        ],
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )


@pytest.fixture()
def alembic_database(tmp_path: Path):
    """A throwaway database file plus a helper to run alembic against it."""
    database = (tmp_path / "app-data" / "alembic-test.db").resolve()
    database.parent.mkdir(parents=True, exist_ok=True)

    def _run(*arguments: str, extra_env: dict[str, str] | None = None):
        result = run_alembic(database, *arguments, extra_env=extra_env)
        return result

    _run.database = database  # type: ignore[attr-defined]
    return _run


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


@pytest.fixture(autouse=True)
def reset_login_limiter_between_tests() -> None:
    """Start every test with empty login-abuse counters.

    The limiter is process-wide state, and every test client shares the same client
    address ("testclient"): without this, failures recorded by one test would start
    refusing another test's logins once the threshold is reached, which would look
    like a flaky suite rather than a leak between tests.
    """
    from app.services.limiter import reset_login_limiter

    reset_login_limiter()
    yield
    reset_login_limiter()


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
def app_session(tmp_path: Path, isolated_application_engine):
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


# --- per-user world helpers ------------------------------------------------
#
# P1.3 scopes every private endpoint to the authenticated user, so tests need an
# actual account plus content owned by it. These helpers build that world on the
# session database the application engine points at, and log the client in so the
# request really travels the authenticated path.

TEST_PASSWORD = "test-password-123"


class World:
    """A temporary user plus helpers for seeding their own content."""

    def __init__(self, factory, client, user_id: int, username: str) -> None:
        self._factory = factory
        self.client = client
        self.user_id = user_id
        self.username = username

    def session(self):
        return self._factory()

    def reload_user(self):
        from app.models import User

        with self.session() as session:
            return session.get(User, self.user_id)

    def lexicon(self, name: str = "test-lexicon", *, visibility: str = "private"):
        """Create (or fetch) a lexicon owned by this user."""
        from sqlalchemy import select

        from app.models import Lexicon, UserLexicon

        with self.session() as session:
            lexicon = session.scalar(
                select(Lexicon).where(
                    Lexicon.owner_user_id == self.user_id, Lexicon.name == name
                )
            )
            if lexicon is None:
                lexicon = Lexicon(
                    owner_user_id=self.user_id,
                    name=name,
                    visibility=visibility,
                    source_type="manual",
                )
                session.add(lexicon)
                session.flush()
            if session.scalar(
                select(UserLexicon).where(
                    UserLexicon.user_id == self.user_id,
                    UserLexicon.lexicon_id == lexicon.id,
                )
            ) is None:
                session.add(UserLexicon(user_id=self.user_id, lexicon_id=lexicon.id))
            session.commit()
            session.refresh(lexicon)
            return lexicon.id

    def add_word(
        self,
        word: str,
        *,
        status: str = "new",
        lexicon_id: int | None = None,
        source_meanings: list[str] | None = None,
        source_raw: str = "",
        anchor: str = "",
        sequence: int | None = None,
    ) -> tuple[int, int]:
        """Create a lexicon entry plus this user's state. Returns (state_id, entry_id).

        The word has no legacy ``word`` row, exactly like a word added from an
        article: it is addressed through ``word_state_id``.
        """
        state_id, entry_id, _legacy = self._create_word(
            word,
            status=status,
            lexicon_id=lexicon_id,
            source_meanings=source_meanings,
            source_raw=source_raw,
            anchor=anchor,
            sequence=sequence,
            legacy=False,
        )
        return state_id, entry_id

    def add_legacy_word(
        self,
        word: str,
        *,
        status: str = "new",
        lexicon_id: int | None = None,
        anchor: str = "",
    ) -> tuple[int, int, int]:
        """A word that also has a V1.1 ``word`` row, like every migrated word.

        Returns (state_id, entry_id, legacy_word_id). Only such a word can be
        addressed through the legacy routes, which take ``word.id``.
        """
        state_id, entry_id, legacy_word_id = self._create_word(
            word,
            status=status,
            lexicon_id=lexicon_id,
            source_meanings=None,
            source_raw="",
            anchor=anchor,
            sequence=None,
            legacy=True,
        )
        assert legacy_word_id is not None, "a legacy word must have a word.id"
        return state_id, entry_id, legacy_word_id

    def _create_word(
        self,
        word: str,
        *,
        status: str,
        lexicon_id: int | None,
        source_meanings: list[str] | None,
        source_raw: str,
        anchor: str,
        sequence: int | None,
        legacy: bool,
    ) -> tuple[int, int, int | None]:
        from app.models import LexiconEntry, UserWordState, Word

        target = lexicon_id if lexicon_id is not None else self.lexicon()
        with self.session() as session:
            entry = LexiconEntry(
                lexicon_id=target,
                word=word,
                normalized_word=word.casefold(),
                source_meanings=source_meanings or [],
                source_raw=source_raw or word,
                default_anchor=anchor or word,
                sequence=sequence,
            )
            session.add(entry)
            session.flush()
            legacy_word_id = None
            if legacy:
                now = datetime.now(UTC).replace(tzinfo=None)
                row = Word(
                    word=word,
                    phonetic="",
                    part_of_speech="",
                    source_meanings=[],
                    source_raw=source_raw or word,
                    anchor=anchor or word,
                    semantic_note="",
                    status=status,
                    first_seen=now,
                    recall_success=0,
                    recall_fail=0,
                    consecutive_failures=0,
                    context_exposure=0,
                    possible_issue=False,
                    notes="",
                    created_at=now,
                    updated_at=now,
                    user_id=self.user_id,
                    lexicon_entry_id=entry.id,
                )
                session.add(row)
                session.flush()
                legacy_word_id = row.id
            state = UserWordState(
                user_id=self.user_id,
                lexicon_entry_id=entry.id,
                legacy_word_id=legacy_word_id,
                status=status,
                anchor_override=anchor or word,
            )
            session.add(state)
            session.commit()
            return state.id, entry.id, legacy_word_id

    def add_article(
        self,
        *,
        title: str = "Test article",
        content: str = "A retained idea about cities and memory.",
        target_words: list[str] | None = None,
        actual_used_words: list[str] | None = None,
        completed: bool = False,
    ) -> int:
        from app.models import Article

        with self.session() as session:
            article = Article(
                user_id=self.user_id,
                title=title,
                content=content,
                target_words=target_words or [],
                actual_used_words=actual_used_words or [],
                completed=completed,
            )
            session.add(article)
            session.commit()
            session.refresh(article)
            return article.id

    def add_import_batch(self, *, status: str = "uploaded") -> int:
        from app.models import ImportBatch

        with self.session() as session:
            batch = ImportBatch(user_id=self.user_id, status=status, stage="upload")
            session.add(batch)
            session.commit()
            session.refresh(batch)
            return batch.id

    def add_review(self, state_id: int, result: str = "know") -> int:
        """Record a review through the state-id namespace.

        ``add_word`` creates words with no legacy row, so this is the only
        namespace that can address them.
        """
        from app.services.study import record_review_for_user_state

        with self.session() as session:
            user = self.reload_user()
            event = record_review_for_user_state(
                session, user, state_id, result, "daily", "recall"
            )
            return event.id


@pytest.fixture()
def make_world(isolated_application_engine):
    """Factory that creates an authenticated user world on the app database."""
    from fastapi.testclient import TestClient

    from app.api.deps import ensure_user_settings
    from app.db import get_session_factory
    from app.models import User
    from app.security import hash_password

    created: list[int] = []

    def _make(username: str, *, role: str = "user", password: str = TEST_PASSWORD) -> World:
        factory = get_session_factory()
        with factory() as session:
            user = User(
                username=username.casefold(),
                display_name=username,
                role=role,
                password_hash=hash_password(password),
            )
            session.add(user)
            session.flush()
            ensure_user_settings(session, user)
            session.commit()
            user_id = user.id
        created.append(user_id)

        from app.main import app

        client = TestClient(app)
        client.__enter__()
        response = client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        assert response.status_code == 200, response.text
        return World(factory, client, user_id, username.casefold())

    yield _make

    for user_id in created:
        with get_session_factory()() as session:
            user = session.get(User, user_id)
            if user is not None:
                session.delete(user)
                session.commit()


@pytest.fixture()
def world(make_world):
    """One authenticated user, torn down with the test."""
    value = make_world("solo-user")
    yield value
    value.client.__exit__(None, None, None)


@pytest.fixture()
def two_worlds(make_world):
    """Two authenticated users for IDOR checks."""
    first = make_world("user-a")
    second = make_world("user-b")
    yield first, second
    first.client.__exit__(None, None, None)
    second.client.__exit__(None, None, None)
