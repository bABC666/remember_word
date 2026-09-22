"""The re-authentication guard: budget, audit and shared resource protection.

Sensitive operations (changing the password today, revoking other sessions next) ask
for the account's password again. This file pins the four properties that make that
guard worth having:

* a refused attempt costs nothing -- the budget is checked before the concurrency
  gate, so a 429 neither occupies a verification slot nor spends a hash;
* every attempt that does run costs **exactly one** verification and gives the slot
  back afterwards, whatever the outcome;
* failures are recorded against the account (audit event plus budget) while a refusal
  is not recorded at all;
* the budget is per account and entirely separate from the login limiter, which is
  keyed per address and anonymous.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import HistoryEvent, UserSession
from app.services.auth import COOKIE_NAME
from app.services.limiter import (
    login_limiter,
    reauth_limiter,
    reset_login_limiter,
    reset_reauth_limiter,
)

PASSWORD = "test-password-123"
NEW_PASSWORD = "another-password-1"
WRONG = "definitely-wrong"
LIMITED_DETAIL = "密码校验尝试过于频繁，请稍后再试"


@pytest.fixture()
def reauth_limits(monkeypatch):
    """Apply re-auth settings, then rebuild the limiters from them."""

    def _apply(*, failures: int = 5, window: int = 300) -> None:
        monkeypatch.setenv("VOCAB_REAUTH_FAILURES", str(failures))
        monkeypatch.setenv("VOCAB_REAUTH_WINDOW_SECONDS", str(window))
        get_settings.cache_clear()
        reset_login_limiter()
        reset_reauth_limiter()

    return _apply


def change_password(world, *, current: str = WRONG, new: str = NEW_PASSWORD):
    return world.client.post(
        "/api/auth/password", json={"current_password": current, "new_password": new}
    )


def reauth_events(world) -> list[HistoryEvent]:
    with world.session() as session:
        return list(
            session.scalars(
                select(HistoryEvent)
                .where(HistoryEvent.event_type == "reauth_failed")
                .order_by(HistoryEvent.id)
            )
        )


def event_ids(world) -> set[int]:
    with world.session() as session:
        return set(session.scalars(select(HistoryEvent.id)).all())


def new_reauth_events(world, before: set[int]) -> list[HistoryEvent]:
    """Events written since ``before``.

    The suite shares one database and reuses row ids after each world is deleted, so
    diffing by event id is the only comparison that stays meaningful.
    """
    return [event for event in reauth_events(world) if event.id not in before]


# --- the budget ------------------------------------------------------------


def test_the_sixth_wrong_password_is_refused_with_429(world, reauth_limits) -> None:
    reauth_limits(failures=5, window=300)

    for attempt in range(1, 6):
        response = change_password(world)
        assert response.status_code == 400, f"attempt {attempt}: {response.text}"
        assert response.json() == {"detail": "当前密码不正确"}

    refused = change_password(world)
    assert refused.status_code == 429, refused.text
    assert refused.json() == {"detail": LIMITED_DETAIL}
    assert refused.headers["retry-after"] == "300"
    assert refused.headers.get("cache-control") == "no-store"


def test_a_successful_verification_clears_the_budget(world, reauth_limits) -> None:
    reauth_limits(failures=3, window=300)

    assert change_password(world).status_code == 400
    assert change_password(world).status_code == 400
    assert reauth_limiter().failure_count(str(world.user_id)) == 2

    # The right password clears the history, then the password is really changed --
    # which also revokes every session, including this one.
    accepted = change_password(world, current=PASSWORD)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json() == {"ok": True}
    assert reauth_limiter().failure_count(str(world.user_id)) == 0
    assert reauth_limiter().is_limited(str(world.user_id)) is False

    # Sign back in with the new password: the budget really is empty, so two fresh
    # failures are 400s rather than the 429 the earlier ones would have produced.
    relogin = world.client.post(
        "/api/auth/login", json={"username": world.username, "password": NEW_PASSWORD}
    )
    assert relogin.status_code == 200, relogin.text
    assert change_password(world, current="wrong-again", new="x-password-1").status_code == 400
    assert change_password(world, current="wrong-again", new="x-password-1").status_code == 400


def test_one_accounts_failures_do_not_affect_another(two_worlds, reauth_limits) -> None:
    reauth_limits(failures=2, window=300)
    a, b = two_worlds

    assert change_password(a).status_code == 400
    assert change_password(a).status_code == 400
    assert change_password(a).status_code == 429

    # B is untouched: its own budget is still empty and the right password works.
    assert reauth_limiter().failure_count(str(b.user_id)) == 0
    assert change_password(b).status_code == 400
    assert change_password(b, current=PASSWORD).status_code == 200


def test_the_budget_is_separate_from_the_login_limiter(world, reauth_limits) -> None:
    """Burning the re-auth budget must not touch the login limiter, or vice versa."""
    reauth_limits(failures=2, window=300)

    for _ in range(3):
        change_password(world)
    assert reauth_limiter().is_limited(str(world.user_id)) is True
    # The login limiter counts per address and saw none of these: they were
    # authenticated requests, not login attempts.
    assert login_limiter().failures.is_limited("testclient") is False

    # And the account can still sign in: the refusal covers sensitive operations only.
    fresh = world.client.post(
        "/api/auth/login", json={"username": world.username, "password": PASSWORD}
    )
    assert fresh.status_code == 200, fresh.text


def test_the_budget_can_be_switched_off(world, reauth_limits) -> None:
    reauth_limits(failures=0, window=300)
    for _ in range(12):
        assert change_password(world).status_code == 400
    assert reauth_limiter().is_limited(str(world.user_id)) is False


def test_the_window_slides_and_the_account_is_forgiven(world, reauth_limits) -> None:
    reauth_limits(failures=2, window=1)
    assert change_password(world).status_code == 400
    assert change_password(world).status_code == 400
    assert change_password(world).status_code == 429

    time.sleep(1.05)

    assert change_password(world).status_code == 400, "the window has slid past the failures"


# --- cost and resource protection -----------------------------------------


def test_every_attempt_verifies_exactly_once_and_a_refusal_verifies_none(
    world, reauth_limits, monkeypatch
) -> None:
    reauth_limits(failures=2, window=300)
    from app.services import auth as auth_service

    calls: list[str] = []
    real = auth_service.verify_password

    def spy(password: str, password_hash: str) -> bool:
        calls.append(password_hash)
        return real(password, password_hash)

    monkeypatch.setattr(auth_service, "verify_password", spy)

    assert change_password(world).status_code == 400
    assert len(calls) == 1, "one attempt, one verification"

    calls.clear()
    assert change_password(world).status_code == 400
    assert len(calls) == 1

    calls.clear()
    assert change_password(world).status_code == 429
    assert calls == [], "a refused attempt must not spend a verification"

    assert login_limiter().gate.in_flight == 0


def test_a_saturated_gate_refuses_without_verifying(world, reauth_limits, monkeypatch) -> None:
    """The shared gate protects this endpoint too, and refusing costs nothing."""
    reauth_limits(failures=5, window=300)
    from app.services import auth as auth_service

    calls: list[str] = []
    real = auth_service.verify_password

    def spy(password: str, password_hash: str) -> bool:
        calls.append(password_hash)
        return real(password, password_hash)

    monkeypatch.setattr(auth_service, "verify_password", spy)

    verifications = login_limiter()
    held = [verifications.gate.acquire() for _ in range(verifications.gate.limit)]
    assert all(held), "every slot must be taken for this test"
    try:
        refused = change_password(world)
    finally:
        for _ in held:
            verifications.gate.release()

    assert refused.status_code == 429, refused.text
    assert refused.headers["retry-after"] == "1"
    assert calls == []
    assert verifications.gate.in_flight == 0


def test_the_shared_gate_is_released_after_every_outcome(
    world, reauth_limits, monkeypatch
) -> None:
    reauth_limits(failures=1, window=300)
    verifications = login_limiter()

    # wrong password -> 400
    assert change_password(world).status_code == 400
    assert verifications.gate.in_flight == 0

    # budget spent -> 429
    assert change_password(world).status_code == 429
    assert verifications.gate.in_flight == 0

    # an exception raised inside the verification must not leak the slot either
    from app.api import auth as auth_api

    real_verify = auth_api.verify_user_password

    def explode(user, password):
        raise RuntimeError("boom")

    reauth_limits(failures=5, window=300)  # fresh budget, so the check really runs
    monkeypatch.setattr(auth_api, "verify_user_password", explode)
    with pytest.raises(RuntimeError):
        change_password(world)
    assert verifications.gate.in_flight == 0, "the slot must be released in a finally"

    # and a successful attempt still works afterwards
    monkeypatch.setattr(auth_api, "verify_user_password", real_verify)
    assert change_password(world, current=PASSWORD).status_code == 200
    assert verifications.gate.in_flight == 0


# --- audit -----------------------------------------------------------------


def test_a_failed_attempt_is_recorded_against_the_account(world, reauth_limits) -> None:
    reauth_limits(failures=5, window=300)
    before = event_ids(world)

    assert change_password(world).status_code == 400

    recorded = new_reauth_events(world, before)
    assert len(recorded) == 1
    event = recorded[0]
    assert event.user_id == world.user_id
    assert event.entity_type == "user"
    assert event.entity_id == world.user_id
    assert event.timestamp is not None
    assert event.payload == {"action": "change_password"}


def test_a_refusal_writes_no_event(world, reauth_limits) -> None:
    """The limiter must not become a write path: a 429 stores nothing."""
    reauth_limits(failures=1, window=300)

    assert change_password(world).status_code == 400
    before = event_ids(world)
    for _ in range(3):
        assert change_password(world).status_code == 429

    assert event_ids(world) == before, "a refused attempt added rows"


def test_the_audit_event_carries_no_secret(world, reauth_limits) -> None:
    reauth_limits(failures=5, window=300)
    token = world.client.cookies.get(COOKIE_NAME)
    before = event_ids(world)

    secret = "submitted-password-should-never-be-stored"
    assert change_password(world, current=secret).status_code == 400

    event = new_reauth_events(world, before)[0]
    # The payload is *only* the fixed action name: nothing else can be in there.
    assert event.payload == {"action": "change_password"}
    rendered = str(event.payload)
    assert secret not in rendered, "the submitted password must never be stored"
    assert token and token not in rendered, "the session token must never be stored"
    with world.session() as session:
        token_hash = session.scalar(
            select(UserSession.token_hash).where(UserSession.user_id == world.user_id)
        )
    assert token_hash and token_hash not in rendered, "the token hash must never be stored"
