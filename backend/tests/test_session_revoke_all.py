"""Bulk session revocation: "sign out my other devices", or every device.

The endpoint is the first sensitive operation to sit behind the Phase 2.7-d-c guard,
so these tests cover both halves: the scope semantics (who gets signed out, and whose
cookie is cleared) and the guard (password required, failures budgeted, one event per
revoked session, nothing disclosed about sessions that are not the caller's).

``scope="all"`` ends the caller's own session, so a repeat call needs a fresh sign-in;
``scope="others"`` is the one that can simply be called twice, which is where
idempotency is asserted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import HistoryEvent, UserSession
from app.services.auth import COOKIE_NAME, create_session
from app.services.limiter import reauth_limiter

PASSWORD = "test-password-123"
WRONG = "definitely-wrong"


def revoke(world, *, scope: str = "others", password: str = PASSWORD):
    return world.client.post(
        "/api/auth/sessions/revoke",
        json={"scope": scope, "current_password": password},
    )


def add_session(world, *, user_agent: str = "other-device", created_at: datetime | None = None):
    """Create another session for this user. Returns (token, session id)."""
    with world.session() as session:
        token, record = create_session(session, world.reload_user(), now=created_at)
        record.user_agent = user_agent
        session.add(record)
        session.commit()
        return token, record.id


def authenticate_as(world, token: str):
    world.client.cookies.clear()
    world.client.cookies.set(COOKIE_NAME, token)
    return world.client.get("/api/auth/me")


def session_rows(world) -> list[UserSession]:
    with world.session() as session:
        return list(
            session.scalars(
                select(UserSession)
                .where(UserSession.user_id == world.user_id)
                .order_by(UserSession.id)
            ).all()
        )


def live_session_ids(world) -> set[int]:
    return {row.id for row in session_rows(world) if row.revoked_at is None}


def revoked_event_ids(world) -> list[int]:
    with world.session() as session:
        return list(
            session.scalars(
                select(HistoryEvent.entity_id)
                .where(HistoryEvent.event_type == "session_revoked")
                .order_by(HistoryEvent.id)
            ).all()
        )


def new_revoked_events(world, before: set[int]) -> list[HistoryEvent]:
    """``session_revoked`` rows written since ``before`` was captured.

    The suite shares one database and reuses row ids after each world is deleted, so
    old events can carry the same ``entity_id`` as this test's sessions: only the
    events' *own* ids identify what this test just wrote.
    """
    with world.session() as session:
        return [
            event
            for event in session.scalars(
                select(HistoryEvent)
                .where(HistoryEvent.event_type == "session_revoked")
                .order_by(HistoryEvent.id)
            ).all()
            if event.id not in before
        ]


def event_row_ids(world) -> set[int]:
    with world.session() as session:
        return set(session.scalars(select(HistoryEvent.id)).all())


# --- basics ----------------------------------------------------------------


def test_anonymous_cannot_revoke_sessions(world) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    _token, other_id = add_session(world)
    with TestClient(app) as anonymous:
        response = anonymous.post(
            "/api/auth/sessions/revoke",
            json={"scope": "all", "current_password": PASSWORD},
        )

    assert response.status_code == 401
    assert other_id in live_session_ids(world), "nothing may be revoked anonymously"


def test_the_password_is_required(world) -> None:
    for body in (
        {"scope": "others"},
        {"scope": "others", "current_password": ""},
        {"scope": "others", "current_password": None},
        {"current_password": PASSWORD},
        {"scope": "everyone", "current_password": PASSWORD},
    ):
        response = world.client.post("/api/auth/sessions/revoke", json=body)
        assert response.status_code == 422, (body, response.text)
    assert live_session_ids(world) == {row.id for row in session_rows(world)}


def test_a_wrong_password_revokes_nothing(world) -> None:
    _token, other_id = add_session(world)
    before = event_row_ids(world)

    response = revoke(world, scope="all", password=WRONG)

    assert response.status_code == 400, response.text
    assert response.json() == {"detail": "当前密码不正确"}
    assert "set-cookie" not in response.headers
    assert live_session_ids(world) == {other_id, session_rows(world)[0].id}
    # The refused attempt is audited as such -- and revokes nothing.
    assert event_row_ids(world) - before, "the failed attempt itself is audited"
    assert new_revoked_events(world, before) == [], "nothing may be revoked"


# --- scope=others ----------------------------------------------------------


def test_others_keeps_the_current_session_and_ends_the_rest(world) -> None:
    current_token = world.client.cookies.get(COOKIE_NAME)
    phone_token, phone_id = add_session(world, user_agent="phone")
    browser_token, browser_id = add_session(world, user_agent="browser")
    assert live_session_ids(world) == {phone_id, browser_id, session_rows(world)[0].id}

    response = revoke(world, scope="others")

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "revoked": 2}
    assert "set-cookie" not in response.headers, "the caller's own cookie is untouched"
    assert live_session_ids(world) == {session_rows(world)[0].id}
    # The caller carries on; the other two devices do not.
    assert world.client.get("/api/auth/me").status_code == 200
    assert authenticate_as(world, phone_token).status_code == 401
    assert authenticate_as(world, browser_token).status_code == 401
    world.client.cookies.clear()
    world.client.cookies.set(COOKIE_NAME, current_token)
    assert world.client.get("/api/auth/me").status_code == 200


def test_others_records_one_event_per_revoked_session(world) -> None:
    _a_token, first_id = add_session(world)
    _b_token, second_id = add_session(world)
    before = event_row_ids(world)

    assert revoke(world, scope="others").json()["revoked"] == 2

    recorded = new_revoked_events(world, before)
    assert sorted(event.entity_id for event in recorded) == sorted([first_id, second_id])
    assert all(event.payload == {"scope": "others"} for event in recorded)


def test_others_is_idempotent_and_writes_no_second_event(world) -> None:
    _token, other_id = add_session(world)

    first = revoke(world, scope="others")
    assert first.status_code == 200 and first.json() == {"ok": True, "revoked": 1}

    before = event_row_ids(world)
    second = revoke(world, scope="others")
    assert second.status_code == 200, second.text
    assert second.json() == {"ok": True, "revoked": 0}, "a repeat must revoke nothing"
    assert new_revoked_events(world, before) == [], "and must not record a second event"
    assert other_id not in live_session_ids(world)


def test_others_with_no_other_sessions_is_a_no_op(world) -> None:
    response = revoke(world, scope="others")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "revoked": 0}
    assert live_session_ids(world) == {session_rows(world)[0].id}


# --- scope=all -------------------------------------------------------------


def test_all_ends_every_session_including_the_callers(world) -> None:
    current_token = world.client.cookies.get(COOKIE_NAME)
    phone_token, _phone_id = add_session(world)
    browser_token, _browser_id = add_session(world)

    response = revoke(world, scope="all")

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "revoked": 3}
    assert live_session_ids(world) == set()
    # Every cookie is dead now, including the one that made the request.
    assert authenticate_as(world, current_token).status_code == 401
    assert authenticate_as(world, phone_token).status_code == 401
    assert authenticate_as(world, browser_token).status_code == 401


def test_all_clears_the_cookie_with_the_issued_attributes(world) -> None:
    response = revoke(world, scope="all")

    assert response.status_code == 200
    deletion = response.headers["set-cookie"].lower()
    assert f"{COOKIE_NAME}=" in deletion
    assert "max-age=0" in deletion
    assert "httponly" in deletion
    assert "path=/" in deletion
    assert "samesite=lax" in deletion
    # The caller is signed out: it can no longer reach its own session list.
    assert world.client.get("/api/auth/sessions").status_code == 401


def test_all_records_one_event_per_session(world) -> None:
    _a_token, first_id = add_session(world)
    current_id = session_rows(world)[0].id
    before = event_row_ids(world)

    assert revoke(world, scope="all").json()["revoked"] == 2

    recorded = new_revoked_events(world, before)
    assert {event.entity_id for event in recorded} == {first_id, current_id}
    assert all(event.payload == {"scope": "all"} for event in recorded)


# --- isolation and the guard ----------------------------------------------


def test_one_accounts_revocation_never_touches_another(two_worlds) -> None:
    a, b = two_worlds
    _a_token, a_other = add_session(a)
    b_token, b_other = add_session(b)

    assert revoke(a, scope="all").json()["revoked"] == 2

    assert live_session_ids(a) == set()
    assert a_other not in live_session_ids(a)
    assert live_session_ids(b) == {b_other, session_rows(b)[0].id}
    assert authenticate_as(b, b_token).status_code == 200
    assert b_other in live_session_ids(b)


def test_the_response_says_nothing_about_which_sessions_exist(world) -> None:
    """Only a count of the caller's own sessions: never an id, never another user's."""
    _token, other_id = add_session(world)
    response = revoke(world, scope="others")

    body = response.json()
    assert set(body) == {"ok", "revoked"}
    assert str(other_id) not in response.text
    assert "session_id" not in response.text


def test_repeated_wrong_passwords_spend_the_reauth_budget(world, reauth_limits) -> None:
    reauth_limits(failures=5, window=300)
    _token, other_id = add_session(world)

    for _ in range(5):
        assert revoke(world, scope="others", password=WRONG).status_code == 400

    refused = revoke(world, scope="others", password=WRONG)
    assert refused.status_code == 429, refused.text
    assert refused.json() == {"detail": "密码校验尝试过于频繁，请稍后再试"}
    assert refused.headers["retry-after"] == "300"
    assert refused.headers.get("cache-control") == "no-store"
    # Nothing was revoked by any of the refused attempts, and the correct password is
    # refused too while the budget is spent -- the guard is about the account.
    assert other_id in live_session_ids(world)
    assert revoke(world, scope="others").status_code == 429
    assert reauth_limiter().is_limited(str(world.user_id)) is True


def test_a_successful_revocation_clears_the_budget(world, reauth_limits) -> None:
    reauth_limits(failures=2, window=300)
    _token, other_id = add_session(world)

    assert revoke(world, scope="others", password=WRONG).status_code == 400
    assert reauth_limiter().failure_count(str(world.user_id)) == 1

    assert revoke(world, scope="others").status_code == 200
    assert reauth_limiter().failure_count(str(world.user_id)) == 0
    assert other_id not in live_session_ids(world)


def test_the_budget_is_per_account(two_worlds, reauth_limits) -> None:
    reauth_limits(failures=2, window=300)
    a, b = two_worlds
    _b_token, b_other = add_session(b)

    assert revoke(a, password=WRONG).status_code == 400
    assert revoke(a, password=WRONG).status_code == 400
    assert revoke(a, password=WRONG).status_code == 429

    # B's budget is untouched, and B can still revoke its own sessions.
    assert reauth_limiter().failure_count(str(b.user_id)) == 0
    assert revoke(b, scope="others").status_code == 200
    assert b_other not in live_session_ids(b)


def test_a_refused_attempt_writes_no_session_revoked_event(world, reauth_limits) -> None:
    reauth_limits(failures=0, window=300)  # budget off, so only the hash decides
    _token, other_id = add_session(world)

    before = event_row_ids(world)
    assert revoke(world, scope="all", password=WRONG).status_code == 400
    assert new_revoked_events(world, before) == []
    assert other_id in live_session_ids(world)


def test_an_expired_session_counts_as_revoked_but_only_once(world) -> None:
    """The revocation set is "not revoked yet", not "still usable".

    An expired or idle session is dead but has no ``revoked_at``, so it is included
    (which is what makes cleanup pick it up sooner); the *second* call is the real
    no-op, because by then every row carries a revocation time.
    """
    now = datetime.now(UTC)
    _token, expired_id = add_session(world, created_at=now - timedelta(days=40))
    with world.session() as session:
        record = session.get(UserSession, expired_id)
        assert record is not None
        record.expires_at = now - timedelta(days=1)
        session.add(record)
        session.commit()

    first = revoke(world, scope="others")
    assert first.status_code == 200
    assert first.json() == {"ok": True, "revoked": 1}

    before = event_row_ids(world)
    second = revoke(world, scope="others")
    assert second.json() == {"ok": True, "revoked": 0}
    assert new_revoked_events(world, before) == []
