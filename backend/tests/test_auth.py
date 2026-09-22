"""Authentication tests: login, sessions, cookies, admin gating, CLI.

Every test runs against its own temporary database and its own temporary engine;
nothing here can reach the real data directory (see
``backend/app/testing_guards.py`` and ``conftest.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import HistoryEvent, User, UserSession
from app.security import UNUSABLE_PASSWORD, hash_password, password_is_usable, verify_password
from app.services.auth import COOKIE_NAME

PASSWORD = "correct-horse-battery"


@pytest.fixture()
def auth_db(tmp_path, isolated_application_engine):
    """A throwaway database plus a session factory bound to it."""
    from sqlalchemy.orm import sessionmaker

    import app.models  # noqa: F401
    from app.db import Base, make_engine
    from app.testing_guards import assert_safe_for_destructive_operation

    database = tmp_path / "app-data" / "auth-test-db.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    assert_safe_for_destructive_operation(database, action="build an auth test schema in")
    engine = make_engine(f"sqlite:///{database.as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture()
def client(auth_db, monkeypatch):
    """A TestClient whose application talks to the throwaway database."""
    import app.db

    monkeypatch.setattr(app.db, "_session_factory", auth_db)
    from app.main import app

    with TestClient(app) as value:
        yield value


def create_user(
    auth_db,
    username: str,
    password: str | None = PASSWORD,
    role: str = "user",
    *,
    is_active: bool = True,
) -> int:
    from app.api.deps import ensure_user_settings

    with auth_db() as session:
        user = User(
            username=username.casefold(),
            display_name=username,
            role=role,
            is_active=is_active,
            password_hash=hash_password(password) if password else UNUSABLE_PASSWORD,
        )
        session.add(user)
        session.flush()
        ensure_user_settings(session, user)
        session.commit()
        return user.id


def test_login_succeeds_and_sets_hardened_cookie(auth_db, client) -> None:
    create_user(auth_db, "cookie-user")
    response = client.post(
        "/api/auth/login", json={"username": "cookie-user", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    assert response.json()["username"] == "cookie-user"
    assert response.json()["is_admin"] is False
    assert "password_hash" not in response.text

    header = response.headers["set-cookie"]
    assert COOKIE_NAME in header
    assert "HttpOnly" in header
    assert "samesite=lax" in header.lower().replace("samesite=lax", "samesite=lax")
    assert "Secure" not in header, "local HTTP development must stay usable"

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "cookie-user"


def test_username_is_case_insensitive(auth_db, client) -> None:
    create_user(auth_db, "mixed-case")
    response = client.post(
        "/api/auth/login", json={"username": "MiXeD-CaSe", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["username"] == "mixed-case"


def test_all_four_login_failures_are_indistinguishable(auth_db, client) -> None:
    """Unknown, wrong password, no password set and disabled: one answer, word for word.

    A distinct answer for any of them -- in body or in status -- tells an
    unauthenticated caller that a username exists, is disabled, or has never been
    given a password.
    """
    create_user(auth_db, "known-user")
    create_user(auth_db, "sentinel-user", password=None)
    create_user(auth_db, "disabled-user", is_active=False)

    cases = {
        "unknown username": {"username": "no-such-user", "password": PASSWORD},
        "wrong password": {"username": "known-user", "password": "definitely-wrong"},
        "no password set": {"username": "sentinel-user", "password": PASSWORD},
        "disabled account": {"username": "disabled-user", "password": PASSWORD},
    }

    for label, payload in cases.items():
        response = client.post("/api/auth/login", json=payload)
        assert response.status_code == 401, label
        assert response.json() == {"detail": "用户名或密码不正确"}, (label, response.json())
        # The refusal must not name the account, its state, or any operator command.
        assert "set-password" not in response.text, label
        assert "尚未设置密码" not in response.text, label
        assert "no-such-user" not in response.text, label
        # And a refusal never issues a session.
        assert "set-cookie" not in response.headers, label

    assert client.get("/api/auth/me").status_code == 401


def test_every_login_path_performs_exactly_one_password_verification(
    auth_db, client, monkeypatch
) -> None:
    """The unknown-username path must do the same hashing work as a real account.

    Verifying against a dummy hash is what removes the timing side channel: without
    it an unknown username answers in about a millisecond while a known one pays
    for Argon2. The work is asserted directly (a call count), not by measuring
    clock time, so the test cannot flake.
    """
    import app.api.auth as auth_module
    from app.security import dummy_password_hash

    real_verify = auth_module.verify_password
    calls: list[tuple[str, str]] = []

    def spy(password: str, password_hash: str) -> bool:
        calls.append((password, password_hash))
        return real_verify(password, password_hash)

    monkeypatch.setattr(auth_module, "verify_password", spy)
    create_user(auth_db, "verified-user")
    create_user(auth_db, "verified-sentinel", password=None)

    with auth_db() as session:
        stored = session.scalar(
            select(User.password_hash).where(User.username == "verified-user")
        )
    assert stored is not None

    def attempt(payload: dict[str, str]) -> int:
        calls.clear()
        response = client.post("/api/auth/login", json=payload)
        assert len(calls) == 1, f"{payload['username']}: {len(calls)} verifications"
        return response.status_code

    dummy = dummy_password_hash()
    # Unknown user: one verification, against a hash of a discarded password.
    assert attempt({"username": "verified-missing", "password": "x"}) == 401
    assert calls[0][1] == dummy, "the unknown-username path must pay for a verification"

    # Existing accounts: one verification, against the account's own hash.
    assert attempt({"username": "verified-user", "password": "wrong"}) == 401
    assert calls[0][1] == stored
    assert attempt({"username": "verified-user", "password": PASSWORD}) == 200
    assert calls[0][1] == stored

    # An account with no password has no usable hash, so it verifies against the
    # dummy as well -- and is refused even if that verification were to match.
    assert attempt({"username": "verified-sentinel", "password": PASSWORD}) == 401
    assert calls[0][1] == dummy

    # A disabled account is verified like any other before being refused.
    create_user(auth_db, "verified-disabled", is_active=False)
    with auth_db() as session:
        disabled_hash = session.scalar(
            select(User.password_hash).where(User.username == "verified-disabled")
        )
    assert attempt({"username": "verified-disabled", "password": PASSWORD}) == 401
    assert calls[0][1] == disabled_hash

    # The dummy hash is never stored anywhere: it belongs to no account.
    with auth_db() as session:
        assert session.scalar(select(User).where(User.password_hash == dummy)) is None


def test_wrong_password_is_rejected_without_leaking_existence(auth_db, client) -> None:
    create_user(auth_db, "existing-user")
    wrong = client.post(
        "/api/auth/login", json={"username": "existing-user", "password": "nope"}
    )
    missing = client.post(
        "/api/auth/login", json={"username": "no-such-user", "password": "nope"}
    )
    assert wrong.status_code == 401
    assert missing.status_code == 401
    # Identical message: the endpoint must not reveal whether a user exists.
    assert wrong.json()["detail"] == missing.json()["detail"]


def test_bootstrap_sentinel_account_cannot_log_in(auth_db, client) -> None:
    create_user(auth_db, "sentinel-user", password=None)
    for attempt in ("", UNUSABLE_PASSWORD, PASSWORD, "anything"):
        response = client.post(
            "/api/auth/login", json={"username": "sentinel-user", "password": attempt or "x"}
        )
        assert response.status_code == 401, attempt
    assert client.get("/api/auth/me").status_code == 401


def test_inactive_user_cannot_log_in(auth_db, client) -> None:
    create_user(auth_db, "disabled-user", is_active=False)
    response = client.post(
        "/api/auth/login", json={"username": "disabled-user", "password": PASSWORD}
    )
    assert response.status_code == 401


def test_raw_token_is_never_stored(auth_db, client) -> None:
    create_user(auth_db, "token-user")
    client.post("/api/auth/login", json={"username": "token-user", "password": PASSWORD})
    raw_token = client.cookies.get(COOKIE_NAME)
    assert raw_token
    with auth_db() as session:
        stored = session.scalars(select(UserSession.token_hash)).all()
    assert raw_token not in stored
    assert any(len(value) == 64 for value in stored)


def test_logout_revokes_the_session_and_clears_the_cookie(auth_db, client) -> None:
    create_user(auth_db, "logout-user")
    client.post("/api/auth/login", json={"username": "logout-user", "password": PASSWORD})
    assert client.get("/api/auth/me").status_code == 200

    response = client.post("/api/auth/logout")
    assert response.status_code == 200
    with auth_db() as session:
        revoked = session.scalars(
            select(UserSession.revoked_at).where(UserSession.revoked_at.is_not(None))
        ).all()
    assert revoked, "logout must revoke the session row"
    assert client.get("/api/auth/me").status_code == 401


#: How a valueless attribute (``HttpOnly``, ``Secure``) is recorded by ``parse_cookie``.
FLAG = "<flag>"


def parse_cookie(header: str) -> dict[str, str]:
    """One Set-Cookie header as {name, attribute: value} with lowercased keys.

    Attributes are always present in the result: absent is ``""``, a valueless
    attribute is :data:`FLAG`. That makes "the two headers agree" a plain dict
    comparison instead of a membership test that silently passes when a typo'd key
    is missing from both.
    """
    parts = [part.strip() for part in header.split(";")]
    attributes = {
        "name": parts[0].split("=", 1)[0],
        "path": "",
        "secure": "",
        "httponly": "",
        "samesite": "",
        "max-age": "",
    }
    for part in parts[1:]:
        key, _, value = part.partition("=")
        attributes[key.strip().lower()] = value.strip().lower() if value else FLAG
    return attributes


def test_logout_deletes_the_cookie_with_the_attributes_it_was_set_with(
    auth_db, client
) -> None:
    """Set and delete must agree, so the deletion keeps working as the cookie evolves.

    Browsers match a deletion on name and path, but a later move behind a
    ``__Host-``/``__Secure-`` prefixed name requires the deletion to carry
    ``Secure`` too -- which is exactly what the two calls used to disagree about.
    """
    create_user(auth_db, "cookie-attributes")
    login = client.post(
        "/api/auth/login", json={"username": "cookie-attributes", "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200, logout.text

    issued = parse_cookie(login.headers["set-cookie"])
    deleted = parse_cookie(logout.headers["set-cookie"])

    assert issued["name"] == deleted["name"] == COOKIE_NAME
    for attribute in ("path", "secure", "httponly", "samesite"):
        assert deleted[attribute] == issued[attribute], (attribute, issued, deleted)
    assert deleted["path"] == "/"
    assert deleted["max-age"] == "0"
    # The hardened attributes are on the deletion, not merely absent from both.
    assert deleted["httponly"] == FLAG, deleted
    assert deleted["samesite"] == "lax", deleted


def test_the_deletion_cookie_is_secure_when_the_session_cookie_is(
    auth_db, client, monkeypatch
) -> None:
    """The attribute that varies by configuration must match on both calls."""
    from app.config import get_settings

    monkeypatch.setenv("VOCAB_COOKIE_SECURE", "true")
    get_settings.cache_clear()
    create_user(auth_db, "cookie-secure")
    login = client.post(
        "/api/auth/login", json={"username": "cookie-secure", "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    issued = parse_cookie(login.headers["set-cookie"])
    assert issued["secure"] == FLAG, issued

    # httpx will not send a Secure cookie over the test client's http:// origin, so
    # the token is re-set without the flag. What is under test is the attributes the
    # *server* emits, not the browser's transport rule.
    client.cookies.clear()
    token = login.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    client.cookies.set(COOKIE_NAME, token)
    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200, logout.text
    deleted = parse_cookie(logout.headers["set-cookie"])

    assert deleted["secure"] == FLAG, deleted
    assert deleted["httponly"] == issued["httponly"] == FLAG, (issued, deleted)
    assert deleted["path"] == issued["path"] == "/", (issued, deleted)
    assert deleted["samesite"] == issued["samesite"] == "lax", (issued, deleted)


def test_auth_responses_are_never_cached(auth_db, client) -> None:
    """Every /api/auth/* answer carries Cache-Control: no-store, refusals included."""
    create_user(auth_db, "cache-user")

    responses = {
        "login failure": client.post(
            "/api/auth/login", json={"username": "cache-missing", "password": "x"}
        ),
        "login success": client.post(
            "/api/auth/login", json={"username": "cache-user", "password": PASSWORD}
        ),
        "me": client.get("/api/auth/me"),
        "password failure": client.post(
            "/api/auth/password",
            json={"current_password": "wrong", "new_password": "another-password-1"},
        ),
        "logout": client.post("/api/auth/logout"),
        "me after logout": client.get("/api/auth/me"),
    }
    statuses = {label: response.status_code for label, response in responses.items()}
    assert statuses == {
        "login failure": 401,
        "login success": 200,
        "me": 200,
        "password failure": 400,
        "logout": 200,
        "me after logout": 401,
    }, statuses
    for label, response in responses.items():
        assert response.headers.get("cache-control") == "no-store", (label, dict(response.headers))

    # Scoped to the auth routes: nothing else gained a header.
    assert "cache-control" not in client.get("/api/health").headers


def test_expired_session_is_rejected(auth_db, client) -> None:
    create_user(auth_db, "expired-user")
    client.post("/api/auth/login", json={"username": "expired-user", "password": PASSWORD})
    with auth_db() as session:
        record = session.scalar(select(UserSession))
        record.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        session.add(record)
        session.commit()
    assert client.get("/api/auth/me").status_code == 401


def test_revoked_session_is_rejected(auth_db, client) -> None:
    create_user(auth_db, "revoked-user")
    client.post("/api/auth/login", json={"username": "revoked-user", "password": PASSWORD})
    with auth_db() as session:
        record = session.scalar(select(UserSession))
        record.revoked_at = datetime.now(UTC).replace(tzinfo=None)
        session.add(record)
        session.commit()
    assert client.get("/api/auth/me").status_code == 401


def test_auth_endpoints_require_authentication(client) -> None:
    assert client.get("/api/auth/me").status_code == 401
    # Logout is deliberately absent from this list: it is idempotent and safe to
    # call without a session, so it can always clear a dead cookie
    # (see test_logout_is_idempotent_in_every_session_state).
    assert client.get("/api/health").status_code == 200


def test_logout_is_idempotent_in_every_session_state(auth_db, client) -> None:
    """Logout always succeeds and always clears the cookie, whatever the session is.

    Refusing a dead session leaves a dead cookie in the browser and tells an
    unauthenticated caller something about the token it presented. Every state
    therefore answers the same 200 with the same body and the same deletion cookie.
    """
    create_user(auth_db, "logout-idempotent")
    login = client.post(
        "/api/auth/login", json={"username": "logout-idempotent", "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    issued = parse_cookie(login.headers["set-cookie"])
    token = login.headers["set-cookie"].split(";")[0].split("=", 1)[1]

    answers = []

    # 1. a live session
    live = client.post("/api/auth/logout")
    answers.append(("live", live))
    with auth_db() as session:
        record = session.scalar(select(UserSession))
        assert record is not None and record.revoked_at is not None
        first_revocation = record.revoked_at

    # 2. the same session again, now revoked (the cookie is replayed)
    client.cookies.set(COOKIE_NAME, token)
    answers.append(("already revoked", client.post("/api/auth/logout")))
    with auth_db() as session:
        assert session.scalar(select(UserSession)).revoked_at == first_revocation, (
            "a repeat logout must not rewrite the original revocation time"
        )

    # 3. an expired session
    client.cookies.clear()
    assert client.post(
        "/api/auth/login", json={"username": "logout-idempotent", "password": PASSWORD}
    ).status_code == 200
    expired_token = client.cookies.get(COOKIE_NAME)
    with auth_db() as session:
        live_record = session.scalars(
            select(UserSession).where(UserSession.revoked_at.is_(None))
        ).one()
        live_record.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
        session.add(live_record)
        session.commit()
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, expired_token)
    answers.append(("expired", client.post("/api/auth/logout")))
    assert client.get("/api/auth/me").status_code == 401, "the expired session stays unusable"

    # 4. an unrecognised token
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, "not-a-real-token")
    answers.append(("unknown token", client.post("/api/auth/logout")))

    # 5. no cookie at all
    client.cookies.clear()
    answers.append(("no cookie", client.post("/api/auth/logout")))

    for label, response in answers:
        assert response.status_code == 200, (label, response.text)
        assert response.json() == {"ok": True}, (label, response.json())
        deleted = parse_cookie(response.headers["set-cookie"])
        # Every state clears the cookie, with the attributes it was issued with.
        assert deleted["name"] == issued["name"] == COOKIE_NAME, label
        assert deleted["max-age"] == "0", label
        for attribute in ("path", "secure", "httponly", "samesite"):
            assert deleted[attribute] == issued[attribute], (label, attribute, deleted, issued)
    assert len({(r.status_code, r.text) for _, r in answers}) == 1, "states must be indistinguishable"


def login_failure_events(auth_db) -> list[HistoryEvent]:
    with auth_db() as session:
        return list(
            session.scalars(
                select(HistoryEvent)
                .where(HistoryEvent.event_type == "login_failed")
                .order_by(HistoryEvent.id)
            )
        )


def login_events(auth_db) -> list[HistoryEvent]:
    with auth_db() as session:
        return list(
            session.scalars(
                select(HistoryEvent)
                .where(HistoryEvent.event_type == "user_login")
                .order_by(HistoryEvent.id)
            )
        )


def session_exists_for(auth_db) -> bool:
    with auth_db() as session:
        return session.scalar(select(UserSession.id)) is not None


def test_a_successful_login_is_attributed_to_the_account(auth_db, client) -> None:
    """user_login carries the account id, not just a name inside the payload."""
    user_id = create_user(auth_db, "audited-user")
    assert client.post(
        "/api/auth/login", json={"username": "audited-user", "password": PASSWORD}
    ).status_code == 200

    events = login_events(auth_db)
    assert len(events) == 1
    event = events[0]
    assert event.user_id == user_id
    assert event.entity_type == "user" and event.entity_id == user_id
    assert event.timestamp is not None
    assert event.payload == {"username": "audited-user"}
    assert PASSWORD not in str(event.payload)


def test_a_failed_login_on_a_real_account_is_recorded_against_it(auth_db, client) -> None:
    """Wrong password, no password set and disabled: one event each, per account."""
    known_id = create_user(auth_db, "attacked-user")
    sentinel_id = create_user(auth_db, "attacked-sentinel", password=None)
    disabled_id = create_user(auth_db, "attacked-disabled", is_active=False)

    attempts = (
        {"username": "attacked-user", "password": "definitely-wrong"},
        {"username": "attacked-sentinel", "password": "definitely-wrong"},
        {"username": "attacked-disabled", "password": "definitely-wrong"},
    )
    for payload in attempts:
        assert client.post("/api/auth/login", json=payload).status_code == 401

    events = login_failure_events(auth_db)
    assert [event.user_id for event in events] == [known_id, sentinel_id, disabled_id]
    assert [event.entity_id for event in events] == [known_id, sentinel_id, disabled_id]
    assert [event.payload.get("username") for event in events] == [
        "attacked-user",
        "attacked-sentinel",
        "attacked-disabled",
    ]
    # The submitted password must never reach the audit trail.
    assert all("definitely-wrong" not in str(event.payload) for event in events)
    # Every refusal is attributable and none of them opened a session.
    assert not session_exists_for(auth_db)


def test_an_unknown_username_failure_records_no_identifier(auth_db, client) -> None:
    """Enumeration attempts are counted, but the attacker's string is not stored."""
    attempted = "who-is-this-supposed-to-be"
    assert client.post(
        "/api/auth/login", json={"username": attempted, "password": PASSWORD}
    ).status_code == 401

    events = login_failure_events(auth_db)
    assert len(events) == 1
    event = events[0]
    assert event.user_id is None
    assert event.entity_id is None
    assert event.payload == {}, "nothing the caller chose may be written down"
    assert attempted not in str(event.payload)
    assert not session_exists_for(auth_db)


def test_normal_user_cannot_use_admin_endpoints(auth_db, client) -> None:
    create_user(auth_db, "plain-user", role="user")
    create_user(auth_db, "victim-user", role="user")
    client.post("/api/auth/login", json={"username": "plain-user", "password": PASSWORD})

    assert client.get("/api/users").status_code == 403
    with auth_db() as session:
        victim_id = session.scalar(select(User.id).where(User.username == "victim-user"))
    assert client.patch(f"/api/users/{victim_id}", json={"role": "admin"}).status_code == 403
    with auth_db() as session:
        role = session.scalar(select(User.role).where(User.id == victim_id))
    assert role == "user", "a rejected request must not change anything"


def test_admin_can_create_and_disable_users(auth_db, client) -> None:
    create_user(auth_db, "root-admin", role="admin")
    client.post("/api/auth/login", json={"username": "root-admin", "password": PASSWORD})

    created = client.post(
        "/api/users",
        json={"username": "created-by-admin", "password": PASSWORD, "role": "user"},
    )
    assert created.status_code == 201, created.text
    listing = client.get("/api/users")
    assert listing.status_code == 200
    assert all("password_hash" not in item for item in listing.json())

    duplicate = client.post(
        "/api/users",
        json={"username": "created-by-admin", "password": PASSWORD, "role": "user"},
    )
    assert duplicate.status_code == 409

    user_id = created.json()["id"]
    disabled = client.patch(f"/api/users/{user_id}", json={"is_active": False})
    assert disabled.status_code == 200
    assert client.post(
        "/api/auth/login", json={"username": "created-by-admin", "password": PASSWORD}
    ).status_code == 401


def test_admin_cannot_lock_themselves_out(auth_db, client) -> None:
    create_user(auth_db, "self-admin", role="admin")
    client.post("/api/auth/login", json={"username": "self-admin", "password": PASSWORD})
    with auth_db() as session:
        my_id = session.scalar(select(User.id).where(User.username == "self-admin"))
    assert client.patch(f"/api/users/{my_id}", json={"is_active": False}).status_code == 400
    assert client.patch(f"/api/users/{my_id}", json={"role": "user"}).status_code == 400
    assert (
        client.patch("/api/users/999999", json={"role": "admin"}).status_code == 404
    ), "a missing user is a 404, not a silent success"


def test_password_change_requires_the_current_password(auth_db, client) -> None:
    create_user(auth_db, "change-user")
    client.post("/api/auth/login", json={"username": "change-user", "password": PASSWORD})

    wrong = client.post(
        "/api/auth/password",
        json={"current_password": "wrong", "new_password": "brand-new-secret"},
    )
    assert wrong.status_code == 400

    ok = client.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": "brand-new-secret"},
    )
    assert ok.status_code == 200
    # Changing a password signs every existing session out.
    assert client.get("/api/auth/me").status_code == 401
    assert client.post(
        "/api/auth/login", json={"username": "change-user", "password": "brand-new-secret"}
    ).status_code == 200


def test_password_hashing_round_trip_and_sentinel_behaviour() -> None:
    digest = hash_password(PASSWORD)
    assert digest != PASSWORD
    assert digest.startswith("$argon2id$")
    assert verify_password(PASSWORD, digest) is True
    assert verify_password("other", digest) is False
    assert password_is_usable(digest) is True
    assert password_is_usable(UNUSABLE_PASSWORD) is False
    assert verify_password(PASSWORD, UNUSABLE_PASSWORD) is False
    assert verify_password(PASSWORD, "") is False
    assert verify_password("", digest) is False


def test_no_registration_endpoint_exists(client) -> None:
    """Public registration is deliberately absent in V1.2."""
    for path in ("/api/auth/register", "/api/register", "/api/users/register"):
        assert client.post(path, json={}).status_code in {404, 405}


def test_cli_sets_passwords_without_printing_them(auth_db, monkeypatch, capsys) -> None:
    from app import cli

    create_user(auth_db, "cli-sentinel", password=None)
    monkeypatch.setattr(cli, "_open_session", lambda: auth_db())
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "cli-set-secret")

    assert cli.main(["list-users"]) == 0
    output = capsys.readouterr().out
    assert "cli-sentinel" in output
    assert "未设置" in output

    new_password = "cli-set-secret"
    # The password is prompted for; there is no --password option by design.
    assert cli.main(["set-password", "cli-sentinel"]) == 0
    assert new_password not in capsys.readouterr().out

    with auth_db() as session:
        user = session.scalar(select(User).where(User.username == "cli-sentinel"))
        assert password_is_usable(user.password_hash)
        assert verify_password(new_password, user.password_hash)


def test_cli_rejects_duplicates_and_short_passwords(auth_db, monkeypatch, capsys) -> None:
    from app import cli

    create_user(auth_db, "cli-existing")
    monkeypatch.setattr(cli, "_open_session", lambda: auth_db())
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "another-secret")

    assert cli.main(["create-user", "cli-existing"]) == 1
    assert "已存在" in capsys.readouterr().err

    monkeypatch.setattr("getpass.getpass", lambda prompt="": "short")
    with pytest.raises(SystemExit):
        cli.main(["create-user", "cli-new-user"])
    with auth_db() as session:
        assert session.scalar(select(User).where(User.username == "cli-new-user")) is None
