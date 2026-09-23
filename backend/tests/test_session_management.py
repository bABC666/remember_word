"""Session management: listing your own sessions and revoking one of them.

Two properties matter more than the happy path and are asserted explicitly:

* the list is scoped by ``user_id`` and carries metadata only -- never the token that
  lives in the cookie, and never ``token_hash``;
* revoking someone else's session id answers exactly like an id that does not exist,
  so the endpoint cannot be used to discover which session ids are real.

Both endpoints are ``/api/auth/*``, so they also inherit the Phase 2.4-a
``Cache-Control: no-store`` middleware.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import HistoryEvent, UserSession
from app.services.auth import COOKIE_NAME, create_session

#: Exactly the fields the endpoint is allowed to return.
SESSION_FIELDS = {"id", "current", "created_at", "last_seen_at", "expires_at", "user_agent"}

#: The password ``conftest.TEST_PASSWORD`` gives every world's user. Revoking a
#: device, like the bulk action, is behind the re-auth guard.
PASSWORD = "test-password-123"
WRONG_PASSWORD = "definitely-wrong"


def add_session(
    world,
    *,
    created_at: datetime | None = None,
    last_seen_at: datetime | None = None,
    revoked: bool = False,
    expired: bool = False,
) -> tuple[str, int]:
    """Create another session for this user. Returns (token, session id)."""
    with world.session() as session:
        token, record = create_session(session, world.reload_user(), now=created_at)
        if last_seen_at is not None:
            record.last_seen_at = last_seen_at
        if revoked:
            record.revoked_at = datetime.now(UTC)
        if expired:
            record.expires_at = datetime.now(UTC) - timedelta(days=1)
        session.add(record)
        session.commit()
        return token, record.id


def revoke(world, session_id: int, password: str = PASSWORD):
    """Revoke one of this world's sessions through the API, password included."""
    # ``TestClient.delete`` takes no ``json`` argument, so the generic verb is used.
    return world.client.request(
        "DELETE", f"/api/auth/sessions/{session_id}", json={"current_password": password}
    )


def listed(world) -> list[dict]:
    response = world.client.get("/api/auth/sessions")
    assert response.status_code == 200, response.text
    return response.json()["sessions"]


def authenticate_as(world, token: str):
    world.client.cookies.clear()
    world.client.cookies.set(COOKIE_NAME, token)
    return world.client.get("/api/auth/me")


def session_rows(world) -> list[UserSession]:
    """This world's own session rows, oldest first."""
    with world.session() as session:
        return list(
            session.scalars(
                select(UserSession)
                .where(UserSession.user_id == world.user_id)
                .order_by(UserSession.id)
            ).all()
        )


def events_for(world, event_type: str) -> list[HistoryEvent]:
    with world.session() as session:
        return list(
            session.scalars(
                select(HistoryEvent)
                .where(HistoryEvent.event_type == event_type)
                .order_by(HistoryEvent.id)
            )
        )


def new_events(world, event_type: str, before: set[int]) -> list[HistoryEvent]:
    """Events of this type written since ``before`` was captured.

    The suite shares one database for the whole session and each world's user is
    deleted when its test ends, so row ids -- and therefore ``user_id`` and
    ``entity_id`` in old audit rows -- can be reused. Diffing by the event's own id
    is the only comparison that stays meaningful.
    """
    return [event for event in events_for(world, event_type) if event.id not in before]


def event_ids(world, event_type: str) -> set[int]:
    return {event.id for event in events_for(world, event_type)}


# --- GET /api/auth/sessions ------------------------------------------------


def test_a_login_can_see_its_own_session(world) -> None:
    _token, unused_id = add_session(world)  # never used, so it has no activity time

    sessions = {item["id"]: item for item in listed(world)}
    assert set(sessions) == {row.id for row in session_rows(world)}
    assert len(sessions) == 2

    current = sessions[session_rows(world)[0].id]
    assert set(current) == SESSION_FIELDS, sorted(current)
    assert current["current"] is True
    assert current["created_at"] is not None and current["expires_at"] is not None
    assert current["user_agent"] == "testclient"
    assert current["last_seen_at"] is not None, (
        "listing the sessions is itself a request, so it counts as activity"
    )

    unused = sessions[unused_id]
    assert unused["current"] is False
    assert unused["last_seen_at"] is None, "a session that was never used has no activity time"


def test_the_list_is_ordered_by_recent_activity(world) -> None:
    now = datetime.now(UTC)
    _oldest_token, oldest_id = add_session(world, created_at=now - timedelta(days=3))
    _middle_token, middle_id = add_session(
        world, created_at=now - timedelta(days=2), last_seen_at=now - timedelta(hours=2)
    )
    _never_used_token, never_used_id = add_session(world, created_at=now - timedelta(hours=1))

    order = [item["id"] for item in listed(world)]
    # Most recently active first: the fixture's own session was just used, then the
    # session never used at all (its creation time is the honest fallback), then the
    # one last seen two hours ago, then the oldest.
    assert order == [session_rows(world)[0].id, never_used_id, middle_id, oldest_id], order


def test_the_list_never_carries_token_material(world) -> None:
    _, other_id = add_session(world)
    response = world.client.get("/api/auth/sessions")
    assert response.status_code == 200

    for item in response.json()["sessions"]:
        assert set(item) == SESSION_FIELDS, sorted(item)
    raw_token = world.client.cookies.get(COOKIE_NAME)
    assert raw_token and raw_token not in response.text, "the cookie value must not be echoed"
    with world.session() as session:
        token_hash = session.get(UserSession, other_id).token_hash
    assert token_hash not in response.text, "the stored verifier must not be exposed"
    assert "token" not in response.text.lower()


def test_only_your_own_sessions_are_listed(two_worlds) -> None:
    a, b = two_worlds
    _a_token, a_extra = add_session(a)
    _b_token, b_extra = add_session(b)

    a_ids = {item["id"] for item in listed(a)}
    b_ids = {item["id"] for item in listed(b)}

    assert a_ids == {row.id for row in session_rows(a)}
    assert b_ids == {row.id for row in session_rows(b)}
    assert a_ids.isdisjoint(b_ids), (a_ids, b_ids)
    assert a_extra in a_ids and b_extra not in a_ids
    assert b_extra in b_ids and a_extra not in b_ids


def test_dead_sessions_are_not_listed(world) -> None:
    now = datetime.now(UTC)
    _live_token, live_id = add_session(world, created_at=now - timedelta(minutes=5))
    _revoked_token, revoked_id = add_session(world, revoked=True)
    _expired_token, expired_id = add_session(
        world, created_at=now - timedelta(days=40), expired=True
    )
    # Idle, not expired: issued ten days ago and never used, well inside its absolute
    # lifetime, so only the idle rule can reject it.
    _idle_token, idle_id = add_session(world, created_at=now - timedelta(days=10))

    ids = {item["id"] for item in listed(world)}
    assert live_id in ids
    assert revoked_id not in ids, "a revoked session cannot be used, so it is not offered"
    assert expired_id not in ids
    assert idle_id not in ids, "an idle session is dead too"


def test_anonymous_cannot_list_sessions(world) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as anonymous:
        response = anonymous.get("/api/auth/sessions")
    assert response.status_code == 401
    assert response.headers.get("cache-control") == "no-store"


# --- DELETE /api/auth/sessions/{id} ---------------------------------------


def test_revoking_a_session_ends_it(world) -> None:
    token, session_id = add_session(world)
    current_token = world.client.cookies.get(COOKIE_NAME)
    assert authenticate_as(world, token).status_code == 200

    world.client.cookies.clear()
    world.client.cookies.set(COOKIE_NAME, current_token)
    response = revoke(world, session_id)

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    assert "set-cookie" not in response.headers, "the caller's own session is unaffected"
    assert session_id not in {item["id"] for item in listed(world)}
    # The revoked session stops working immediately, on its very next request.
    assert authenticate_as(world, token).status_code == 401
    # And the session that did the revoking still works.
    assert authenticate_as(world, current_token).status_code == 200


def test_revoking_someone_elses_session_looks_exactly_like_an_unknown_one(two_worlds) -> None:
    a, b = two_worlds
    b_token, b_session_id = add_session(b)

    foreign = revoke(a, b_session_id)
    unknown = revoke(a, 999999)

    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json(), (foreign.json(), unknown.json())
    # B's session is untouched, and A's own sessions are all still listed.
    assert authenticate_as(b, b_token).status_code == 200
    assert b_session_id in {item["id"] for item in listed(b)}
    assert {item["id"] for item in listed(a)} == {row.id for row in session_rows(a)}


def test_revoking_twice_is_idempotent(world) -> None:
    _token, session_id = add_session(world)
    before = event_ids(world, "session_revoked")

    first = revoke(world, session_id)
    second = revoke(world, session_id)

    assert first.status_code == 200 and second.status_code == 200
    assert first.json() == second.json() == {"ok": True}
    recorded = new_events(world, "session_revoked", before)
    assert len(recorded) == 1, "a repeat request must not add a second audit event"
    assert recorded[0].entity_id == session_id
    with world.session() as session:
        assert session.get(UserSession, session_id).revoked_at is not None


def test_revoking_the_current_session_clears_the_cookie(world) -> None:
    current_id = session_rows(world)[0].id

    response = revoke(world, current_id)

    assert response.status_code == 200, response.text
    deletion = response.headers["set-cookie"].lower()
    assert f"{COOKIE_NAME}=" in deletion
    assert "max-age=0" in deletion
    assert "httponly" in deletion
    assert "path=/" in deletion
    assert "samesite=lax" in deletion
    # The client is signed out: the cookie it still holds is dead.
    assert world.client.get("/api/auth/me").status_code == 401
    with world.session() as session:
        assert session.get(UserSession, current_id).revoked_at is not None


def test_revoking_records_an_audit_event_without_any_token(world) -> None:
    token, session_id = add_session(world)
    before = event_ids(world, "session_revoked")

    assert revoke(world, session_id).status_code == 200

    recorded = new_events(world, "session_revoked", before)
    assert len(recorded) == 1
    event = recorded[0]
    assert event.user_id == world.user_id
    assert event.entity_type == "user_session"
    assert event.entity_id == session_id
    assert event.timestamp is not None
    assert event.payload == {"current": False}
    assert token not in str(event.payload)
    assert "token" not in str(event.payload).lower()


def test_anonymous_cannot_revoke_a_session(world) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    _token, session_id = add_session(world)
    with TestClient(app) as anonymous:
        response = anonymous.request(
            "DELETE",
            f"/api/auth/sessions/{session_id}",
            json={"current_password": PASSWORD},
        )

    assert response.status_code == 401
    with world.session() as session:
        assert session.get(UserSession, session_id).revoked_at is None


# --- the re-auth guard on a single session ---------------------------------


def test_a_password_is_required_to_revoke_one_session(world) -> None:
    """``DELETE`` changes the credential set, so it needs the caller's password."""
    _token, session_id = add_session(world)
    before = event_ids(world, "session_revoked")

    for body in (None, {}, {"current_password": ""}, {"current_password": None}):
        response = world.client.request(
            "DELETE", f"/api/auth/sessions/{session_id}", json=body
        )
        assert response.status_code == 422, (body, response.text)

    with world.session() as session:
        assert session.get(UserSession, session_id).revoked_at is None
    assert new_events(world, "session_revoked", before) == []


def test_a_wrong_password_revokes_nothing(world) -> None:
    _token, session_id = add_session(world)
    before = event_ids(world, "session_revoked")

    response = revoke(world, session_id, password=WRONG_PASSWORD)

    assert response.status_code == 400, response.text
    assert response.json() == {"detail": "当前密码不正确"}
    assert session_id in {item["id"] for item in listed(world)}
    with world.session() as session:
        assert session.get(UserSession, session_id).revoked_at is None
    assert new_events(world, "session_revoked", before) == []
    # The refused attempt is audited against the account, and never carries the
    # password that was typed.
    failures = events_for(world, "reauth_failed")
    assert failures, "a refused re-auth must be recorded"
    assert failures[-1].payload == {"action": "revoke_session"}
    assert WRONG_PASSWORD not in str(failures[-1].payload)
