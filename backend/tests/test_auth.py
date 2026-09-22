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

from app.models import User, UserSession
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
    assert client.post("/api/auth/logout").status_code == 401
    # Health stays anonymous for deployment probes.
    assert client.get("/api/health").status_code == 200


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

    assert cli.main(["list-users"]) == 0
    output = capsys.readouterr().out
    assert "cli-sentinel" in output
    assert "未设置" in output

    new_password = "cli-set-secret"
    assert cli.main(["set-password", "cli-sentinel", "--password", new_password]) == 0
    assert new_password not in capsys.readouterr().out

    with auth_db() as session:
        user = session.scalar(select(User).where(User.username == "cli-sentinel"))
        assert password_is_usable(user.password_hash)
        assert verify_password(new_password, user.password_hash)


def test_cli_rejects_duplicates_and_short_passwords(auth_db, monkeypatch, capsys) -> None:
    from app import cli

    create_user(auth_db, "cli-existing")
    monkeypatch.setattr(cli, "_open_session", lambda: auth_db())

    assert cli.main(["create-user", "cli-existing", "--password", "another-secret"]) == 1
    assert "已存在" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        cli.main(["create-user", "cli-new-user", "--password", "short"])
    with auth_db() as session:
        assert session.scalar(select(User).where(User.username == "cli-new-user")) is None
