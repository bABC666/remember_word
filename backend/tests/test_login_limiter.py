"""The login abuse guards: the concurrency gate and the per-address failure window.

Both are process-wide state, so every test starts from a fresh limiter (see
``conftest.reset_login_limiter_between_tests``) and configures its own limits through
the environment.

The two properties worth protecting above all others, because getting either wrong
would undo an earlier phase:

* the guards run *before* any hashing, so a refused attempt costs nothing -- the
  point of the exercise, since one Argon2id verification is 64 MiB and ~70 ms;
* a refusal says nothing about the username, so being limited cannot become a new way
  to enumerate accounts (the M1/M2 fixes of Phase 2.4-a).
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.models import HistoryEvent, User, UserSession
from app.security import hash_password
from app.services.limiter import (
    LoginFailureWindow,
    LoginGate,
    login_limiter,
    reset_login_limiter,
)

PASSWORD = "correct-horse-battery"
WRONG = "definitely-wrong"
LIMITED_DETAIL = "登录尝试过于频繁，请稍后再试"


@pytest.fixture()
def login_db(tmp_path, isolated_application_engine):
    """A throwaway database, plus a session factory bound to it."""
    import app.models  # noqa: F401
    from app.db import Base, make_engine
    from app.testing_guards import assert_safe_for_destructive_operation

    database = tmp_path / "app-data" / "login-limiter-db.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    assert_safe_for_destructive_operation(database, action="build a limiter test schema in")
    engine = make_engine(f"sqlite:///{database.as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture()
def client(login_db, monkeypatch):
    """A TestClient whose application talks to the throwaway database."""
    import app.db

    monkeypatch.setattr(app.db, "_session_factory", login_db)
    from app.main import app

    with TestClient(app) as value:
        yield value


@pytest.fixture()
def limits(monkeypatch):
    """Apply login-limit settings, then rebuild the limiter from them."""

    def _apply(*, max_concurrent: int = 8, failures: int = 10, window: int = 300) -> None:
        monkeypatch.setenv("VOCAB_LOGIN_MAX_CONCURRENT", str(max_concurrent))
        monkeypatch.setenv("VOCAB_LOGIN_IP_FAILURES", str(failures))
        monkeypatch.setenv("VOCAB_LOGIN_IP_WINDOW_SECONDS", str(window))
        get_settings.cache_clear()
        reset_login_limiter()

    return _apply


@pytest.fixture()
def make_user(login_db):
    def _make(username: str) -> int:
        with login_db() as session:
            user = User(
                username=username.casefold(),
                display_name=username,
                password_hash=hash_password(PASSWORD),
            )
            session.add(user)
            session.commit()
            return user.id

    return _make


def login(client: TestClient, username: str, password: str = PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def row_counts(login_db) -> tuple[int, int]:
    """(history_event rows, user_session rows) -- what a refused login must not add."""
    with login_db() as session:
        events = session.scalar(select(func.count()).select_from(HistoryEvent)) or 0
        sessions = session.scalar(select(func.count()).select_from(UserSession)) or 0
        return events, sessions


# --- the concurrency gate --------------------------------------------------


def test_the_gate_refuses_instead_of_waiting(limits, client, make_user) -> None:
    limits(max_concurrent=1)
    make_user("gated-user")
    limiter = login_limiter()
    assert limiter.gate.acquire() is True, "the single slot must be taken for this test"
    try:
        response = login(client, "gated-user", WRONG)
    finally:
        limiter.gate.release()

    assert response.status_code == 429, response.text
    assert response.json() == {"detail": LIMITED_DETAIL}
    assert response.headers["retry-after"] == "1"
    # Refusals are still private responses (Phase 2.4-a middleware).
    assert response.headers.get("cache-control") == "no-store"


def test_a_request_refused_by_the_gate_is_never_hashed(limits, client, make_user, monkeypatch) -> None:
    """The gate has to run before Argon2, otherwise it protects nothing."""
    limits(max_concurrent=1)
    make_user("unhashed-user")
    # Since Phase 2.7-d-b every verification goes through app.services.auth, so that
    # is where a spy sees all of them (one place, not one per call site).
    from app.services import auth as auth_service

    calls: list[tuple[str, str]] = []
    real_verify = auth_service.verify_password

    def spy(password: str, password_hash: str) -> bool:
        calls.append((password, password_hash))
        return real_verify(password, password_hash)

    monkeypatch.setattr(auth_service, "verify_password", spy)
    limiter = login_limiter()
    assert limiter.gate.acquire() is True
    try:
        assert login(client, "unhashed-user", WRONG).status_code == 429
    finally:
        limiter.gate.release()

    assert calls == [], "a refused request must not pay for a password verification"


def test_the_gate_is_released_after_every_outcome(limits, client, make_user) -> None:
    limits(max_concurrent=1)
    make_user("released-user")

    assert login(client, "released-user", WRONG).status_code == 401
    assert login_limiter().gate.in_flight == 0, "a refused login must release its slot"

    assert login(client, "released-user").status_code == 200
    assert login_limiter().gate.in_flight == 0, "a successful login must release its slot"

    # A request that never reaches the verification branch (no such user) too.
    assert login(client, "no-such-user", WRONG).status_code == 401
    assert login_limiter().gate.in_flight == 0


def test_concurrent_acquisitions_never_exceed_the_limit() -> None:
    """The gate is what makes several workers safe, so prove it under real threads."""
    gate = LoginGate(3)
    granted = 0
    granted_lock = threading.Lock()
    start = threading.Barrier(40)

    def worker() -> None:
        nonlocal granted
        start.wait()
        if gate.acquire():
            with granted_lock:
                granted += 1

    threads = [threading.Thread(target=worker) for _ in range(40)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert granted == 3
    assert gate.in_flight == 3


# --- the per-address failure window ----------------------------------------


def test_failures_from_one_address_reach_the_threshold(limits, client, make_user) -> None:
    limits(failures=3, window=300)
    make_user("limited-user")

    for attempt in range(3):
        response = login(client, "limited-user", WRONG)
        assert response.status_code == 401, f"attempt {attempt + 1} should still be checked"

    limited = login(client, "limited-user", WRONG)
    assert limited.status_code == 429, limited.text
    assert limited.json() == {"detail": LIMITED_DETAIL}
    assert limited.headers["retry-after"] == "300"
    # Even the correct password is refused while the address is limited: the guard
    # is about the address, not about credentials.
    assert login(client, "limited-user").status_code == 429


def test_a_refused_address_is_never_hashed(limits, client, make_user, monkeypatch) -> None:
    limits(failures=1)
    make_user("unhashed-limited")
    from app.services import auth as auth_service

    calls: list[tuple[str, str]] = []
    real_verify = auth_service.verify_password

    def spy(password: str, password_hash: str) -> bool:
        calls.append((password, password_hash))
        return real_verify(password, password_hash)

    monkeypatch.setattr(auth_service, "verify_password", spy)

    assert login(client, "unhashed-limited", WRONG).status_code == 401
    assert len(calls) == 1
    calls.clear()
    assert login(client, "unhashed-limited", WRONG).status_code == 429
    assert calls == [], "the address limit must be checked before hashing"


def test_the_window_slides_and_the_address_is_forgiven(limits, client, make_user) -> None:
    limits(failures=2, window=1)
    make_user("window-user")

    assert login(client, "window-user", WRONG).status_code == 401
    assert login(client, "window-user", WRONG).status_code == 401
    assert login(client, "window-user", WRONG).status_code == 429

    time.sleep(1.05)

    assert login(client, "window-user", WRONG).status_code == 401, (
        "once the window has slid past the failures, the address is checked again"
    )


def test_a_successful_login_forgets_the_earlier_failures(limits, client, make_user) -> None:
    limits(failures=2, window=300)
    make_user("forgiven-user")

    assert login(client, "forgiven-user", WRONG).status_code == 401
    assert login_limiter().failures.failure_count("testclient") == 1

    assert login(client, "forgiven-user").status_code == 200
    assert login_limiter().failures.failure_count("testclient") == 0
    assert login_limiter().failures.is_limited("testclient") is False

    # Without the reset this third failure would already have been refused (2 >= 2).
    assert login(client, "forgiven-user", WRONG).status_code == 401
    assert login(client, "forgiven-user", WRONG).status_code == 401
    assert login(client, "forgiven-user", WRONG).status_code == 429


def test_being_limited_says_nothing_about_the_username(limits, client, make_user) -> None:
    """Same limits, same address, one real account and one imaginary: same answer."""
    limits(failures=2, window=300)
    make_user("real-account")

    for _ in range(2):
        assert login(client, "real-account", WRONG).status_code == 401
    existing = login(client, "real-account", WRONG)

    reset_login_limiter()  # same configuration, empty counters
    for _ in range(2):
        assert login(client, "imaginary-account", WRONG).status_code == 401
    missing = login(client, "imaginary-account", WRONG)

    assert existing.status_code == missing.status_code == 429
    assert existing.json() == missing.json() == {"detail": LIMITED_DETAIL}
    assert existing.headers["retry-after"] == missing.headers["retry-after"] == "300"
    assert "real-account" not in existing.text
    assert "imaginary-account" not in missing.text


def test_the_limit_can_be_switched_off(limits, client, make_user) -> None:
    limits(failures=0)
    make_user("unlimited-user")
    for _ in range(15):
        assert login(client, "unlimited-user", WRONG).status_code == 401


# --- bounds and side effects ----------------------------------------------


def test_the_failure_window_is_bounded() -> None:
    """Neither the number of addresses nor the per-address history may grow freely."""
    window = LoginFailureWindow(threshold=5, window_seconds=300, max_keys=4)
    for index in range(500):
        window.record_failure(f"10.0.0.{index}", now=1000.0)
    assert window.tracked() <= 4, "the tracked-address map must stay bounded"

    for _ in range(500):
        window.record_failure("10.9.9.9", now=1000.0)
    assert window.failure_count("10.9.9.9", now=1000.0) <= 5 + 1, (
        "per-address timestamps must stay bounded at threshold + 1"
    )
    assert window.is_limited("10.9.9.9", now=1000.0) is True


def test_the_window_uses_an_injectable_clock() -> None:
    """Time-based behaviour is tested by moving the clock, not by sleeping."""
    now = [1000.0]
    window = LoginFailureWindow(threshold=2, window_seconds=60, clock=lambda: now[0])
    window.record_failure("10.1.1.1")
    window.record_failure("10.1.1.1")
    assert window.is_limited("10.1.1.1") is True
    assert window.retry_after("10.1.1.1") == 60

    now[0] += 30
    assert window.is_limited("10.1.1.1") is True, "still inside the window"
    assert window.retry_after("10.1.1.1") == 30

    now[0] += 30
    assert window.is_limited("10.1.1.1") is False, "the oldest failure has expired"


def test_the_address_limit_writes_nothing_beyond_the_recorded_failures(limits, client, login_db, make_user) -> None:
    limits(failures=2, window=300)
    make_user("quiet-user")

    assert login(client, "quiet-user", WRONG).status_code == 401
    assert login(client, "quiet-user", WRONG).status_code == 401
    events_after_failures, sessions_after_failures = row_counts(login_db)
    assert events_after_failures == 2, "each checked failure is recorded once"
    assert sessions_after_failures == 0

    assert login(client, "quiet-user", WRONG).status_code == 429
    assert row_counts(login_db) == (events_after_failures, sessions_after_failures), (
        "the refused request must add neither an audit event nor a session"
    )


def test_the_gate_refusal_writes_nothing(limits, client, login_db, make_user) -> None:
    limits(max_concurrent=1, failures=10)
    make_user("gate-quiet-user")
    before = row_counts(login_db)

    limiter = login_limiter()
    assert limiter.gate.acquire() is True
    try:
        assert login(client, "gate-quiet-user", WRONG).status_code == 429
    finally:
        limiter.gate.release()

    assert row_counts(login_db) == before
