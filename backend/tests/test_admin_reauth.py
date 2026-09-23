"""S-1: the two administrator endpoints behind the re-auth guard.

Before this phase, ``POST /api/users`` and ``PATCH /api/users/{id}`` needed only a
session with ``role=admin``. Everything they do is a credential decision -- create
an account (including an administrator), grant or remove administrator rights,
disable an account, replace someone's password -- so a stolen cookie was enough to
install a permanent backdoor or take over any account.

What this file pins, in the order the design document
(``docs/V1.2-PHASE2.8-A-ADMIN-REAUTH-DESIGN.md``) states it:

* **every** call to either endpoint demands the caller's own password, whichever
  account fields it carries -- there is no "this one is harmless" exemption;
* capability is decided before the body contract (403, never 422), and the password
  is verified before anything is read or written (so a refused request cannot even
  tell a real account id from a made-up one);
* the guard is the *existing* one: same per-account budget, same shared verification
  gate, same ``reauth_failed`` audit, same 400/429 bodies -- reused, not copied;
* a refused request changes no account, creates no session and revokes none: the
  victim's own session still works afterwards;
* no password, token or ``token_hash`` ever reaches a response or an audit payload,
  and nothing under ``/api/users`` is cacheable.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import HistoryEvent, User, UserSession
from app.security import verify_password
from app.services.auth import COOKIE_NAME
from app.services.limiter import login_limiter, reauth_limiter

#: ``conftest.TEST_PASSWORD``: the password every ``make_world`` account is made with.
PASSWORD = "test-password-123"
WRONG = "definitely-wrong"
NEW_PASSWORD = "created-account-pw-1"
ANOTHER_PASSWORD = "replacement-pw-99"
RENAMED = "改名后的显示名"

#: The name every account these tests create through the API gets, so the cleanup
#: fixture below knows exactly which rows are its own.
CREATED_PREFIX = "s1-created"

WRONG_DETAIL = "当前密码不正确"
LIMITED_DETAIL = "密码校验尝试过于频繁，请稍后再试"

#: Marks "this field is not in the body at all", which is different from sending an
#: empty string or ``null``: all three must be refused, but only one of them is a
#: missing field.
OMIT = object()


def create_body(
    username: str = CREATED_PREFIX,
    *,
    current: object = OMIT,
    role: str = "user",
    password: str = NEW_PASSWORD,
) -> dict[str, object]:
    body: dict[str, object] = {"username": username, "password": password, "role": role}
    if current is not OMIT:
        body["current_password"] = current
    return body


def patch_body(*, current: object = OMIT, **fields: object) -> dict[str, object]:
    body: dict[str, object] = dict(fields)
    if current is not OMIT:
        body["current_password"] = current
    return body


def find_account(world, username: str) -> User | None:
    with world.session() as session:
        return session.scalar(select(User).where(User.username == username.casefold()))


def account_state(world, user_id: int) -> dict[str, object]:
    """The target account's row, as the fields a refused request must not touch."""
    with world.session() as session:
        user = session.get(User, user_id)
        assert user is not None, "the account under test has to exist"
        return {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
            "is_active": user.is_active,
            "password_hash": user.password_hash,
        }


def live_session_ids(world, user_id: int) -> set[int]:
    """Session rows that can still be used. Creating one or revoking one shows here."""
    with world.session() as session:
        return set(
            session.scalars(
                select(UserSession.id).where(
                    UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
                )
            ).all()
        )


def event_ids(world) -> set[int]:
    with world.session() as session:
        return set(session.scalars(select(HistoryEvent.id)).all())


def events_of(world, event_type: str) -> list[HistoryEvent]:
    with world.session() as session:
        return list(
            session.scalars(
                select(HistoryEvent)
                .where(HistoryEvent.event_type == event_type)
                .order_by(HistoryEvent.id)
            )
        )


def new_events(world, event_type: str, before: set[int]) -> list[HistoryEvent]:
    """Events of one type written since ``before``.

    The suite shares one database and reuses row ids after each world is deleted, so
    diffing by id (rather than by count) is the only comparison that stays meaningful.
    """
    return [event for event in events_of(world, event_type) if event.id not in before]


@pytest.fixture()
def admin(make_world):
    """A signed-in administrator."""
    value = make_world("s1-admin", role="admin")
    yield value
    value.client.__exit__(None, None, None)


@pytest.fixture()
def target(make_world):
    """An ordinary account, to be the object of an administrator's request."""
    value = make_world("s1-target")
    yield value
    value.client.__exit__(None, None, None)


@pytest.fixture(autouse=True)
def remove_created_accounts():
    """Delete the accounts these tests create through the API.

    ``make_world`` cleans up the worlds it builds, but an account created through the
    endpoint is invisible to it; leaving those rows behind would grow the shared test
    database test after test.
    """
    yield
    from app.db import get_session_factory

    with get_session_factory()() as session:
        for user in session.scalars(
            select(User).where(User.username.like(f"{CREATED_PREFIX}%"))
        ).all():
            session.delete(user)
        session.commit()


# --- the body contract ------------------------------------------------------


def test_creating_an_account_needs_the_admins_own_password(admin) -> None:
    """A missing, empty or null password is refused before anything happens."""
    before = event_ids(admin)

    for body in (create_body(current=OMIT), create_body(current=""), create_body(current=None)):
        response = admin.client.post("/api/users", json=body)
        assert response.status_code == 422, (body, response.text)

    assert find_account(admin, CREATED_PREFIX) is None
    assert event_ids(admin) == before, "a rejected body must not write anything"


def test_updating_an_account_needs_the_admins_own_password(admin, target) -> None:
    before_state = account_state(target, target.user_id)
    before = event_ids(admin)

    for body in (
        patch_body(role="admin"),
        patch_body(current="", role="admin"),
        patch_body(current=None, role="admin"),
    ):
        response = admin.client.patch(f"/api/users/{target.user_id}", json=body)
        assert response.status_code == 422, (body, response.text)

    assert account_state(target, target.user_id) == before_state
    assert event_ids(admin) == before


def test_a_wrong_password_creates_no_account(admin) -> None:
    before = event_ids(admin)

    response = admin.client.post("/api/users", json=create_body(current=WRONG))

    assert response.status_code == 400
    assert response.json() == {"detail": WRONG_DETAIL}
    assert find_account(admin, CREATED_PREFIX) is None
    # The refused attempt is recorded against the caller, with a fixed action name.
    recorded = new_events(admin, "reauth_failed", before)
    assert len(recorded) == 1
    assert recorded[0].user_id == admin.user_id
    assert recorded[0].entity_type == "user"
    assert recorded[0].payload == {"action": "create_user"}
    assert WRONG not in str(recorded[0].payload)


def test_the_right_password_creates_a_usable_account(admin) -> None:
    created = admin.client.post("/api/users", json=create_body(current=PASSWORD))

    assert created.status_code == 201, created.text
    assert created.json()["username"] == CREATED_PREFIX
    assert created.json()["role"] == "user"
    assert created.headers["cache-control"] == "no-store"

    # Creating an account signs nobody in: not the new account, not the caller.
    record = find_account(admin, CREATED_PREFIX)
    assert record is not None
    assert live_session_ids(admin, record.id) == set()

    # The account is real and really holds the password that was sent.
    from app.main import app

    with TestClient(app) as fresh:
        signed_in = fresh.post(
            "/api/auth/login", json={"username": CREATED_PREFIX, "password": NEW_PASSWORD}
        )
    assert signed_in.status_code == 200, signed_in.text


def test_creating_an_administrator_needs_the_password_too(admin) -> None:
    """The backdoor in the S-1 register entry: a second, attacker-owned admin."""
    refused = admin.client.post("/api/users", json=create_body(role="admin", current=WRONG))
    assert refused.status_code == 400
    assert find_account(admin, CREATED_PREFIX) is None

    allowed = admin.client.post("/api/users", json=create_body(role="admin", current=PASSWORD))
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["is_admin"] is True


# --- every operation, not just the alarming ones ----------------------------

#: Each account field the endpoint can change, plus the "changes nothing" case.
UPDATE_CASES = [
    pytest.param({"display_name": RENAMED}, {"display_name": RENAMED}, id="display_name"),
    pytest.param({"role": "admin"}, {"role": "admin"}, id="role"),
    pytest.param({"is_active": False}, {"is_active": False}, id="is_active"),
    pytest.param({"password": ANOTHER_PASSWORD}, {}, id="password"),
    pytest.param({}, {}, id="nothing"),
]


@pytest.mark.parametrize("fields,expected", UPDATE_CASES)
def test_every_update_is_refused_without_the_password(
    admin, target, fields, expected, reauth_limits
) -> None:
    """No exemption for the mild fields, and none for a body that changes nothing."""
    reauth_limits(failures=50, window=300)
    before_state = account_state(target, target.user_id)
    before_sessions = live_session_ids(target, target.user_id)

    missing = admin.client.patch(f"/api/users/{target.user_id}", json=patch_body(**fields))
    assert missing.status_code == 422, missing.text

    wrong = admin.client.patch(
        f"/api/users/{target.user_id}", json=patch_body(current=WRONG, **fields)
    )
    assert wrong.status_code == 400, wrong.text
    assert wrong.json() == {"detail": WRONG_DETAIL}

    assert account_state(target, target.user_id) == before_state
    assert live_session_ids(target, target.user_id) == before_sessions


@pytest.mark.parametrize("fields,expected", UPDATE_CASES)
def test_every_update_succeeds_with_the_password(admin, target, fields, expected) -> None:
    response = admin.client.patch(
        f"/api/users/{target.user_id}", json=patch_body(current=PASSWORD, **fields)
    )

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    state = account_state(target, target.user_id)
    for key, wanted in expected.items():
        assert state[key] == wanted, key
    if "password" in fields:
        assert verify_password(ANOTHER_PASSWORD, state["password_hash"])
        assert not verify_password(PASSWORD, state["password_hash"])


def test_a_refused_update_leaves_the_victim_signed_in(admin, target, reauth_limits) -> None:
    """The property that makes the guard worth having: no effect at all.

    Disabling an account, or replacing its password, also revokes every session it
    has. Those are exactly the operations a refused request must not have performed,
    and the honest way to check is that the victim can still use their session.
    """
    reauth_limits(failures=50, window=300)
    before_state = account_state(target, target.user_id)
    before_sessions = live_session_ids(target, target.user_id)
    before = event_ids(admin)
    assert target.client.get("/api/auth/me").status_code == 200
    assert before_sessions, "the victim must start with a live session"

    for body in (
        patch_body(current=WRONG, password=ANOTHER_PASSWORD),
        patch_body(current=WRONG, is_active=False),
        patch_body(current=WRONG, role="admin"),
        patch_body(current=WRONG, display_name=RENAMED),
    ):
        response = admin.client.patch(f"/api/users/{target.user_id}", json=body)
        assert response.status_code == 400, (body, response.text)

    assert account_state(target, target.user_id) == before_state
    assert live_session_ids(target, target.user_id) == before_sessions
    assert target.client.get("/api/auth/me").status_code == 200, "the victim is still signed in"
    # Nothing but the four failures was written.
    assert new_events(admin, "user_updated", before) == []
    assert new_events(admin, "session_revoked", before) == []
    assert new_events(admin, "user_password_changed", before) == []
    assert len(new_events(admin, "reauth_failed", before)) == 4


def test_a_refused_request_cannot_probe_whether_the_account_exists(admin, target) -> None:
    """The password is checked before the target is looked up, so both answer 400."""
    existing = admin.client.patch(
        f"/api/users/{target.user_id}", json=patch_body(current=WRONG, role="admin")
    )
    missing = admin.client.patch("/api/users/99999999", json=patch_body(current=WRONG, role="admin"))

    assert existing.status_code == missing.status_code == 400
    assert existing.json() == missing.json() == {"detail": WRONG_DETAIL}

    # With the right password the two differ -- and that discloses nothing, because
    # an administrator can already list every account through GET /api/users.
    assert (
        admin.client.patch(
            f"/api/users/{target.user_id}", json=patch_body(current=PASSWORD, role="admin")
        ).status_code
        == 200
    )
    assert (
        admin.client.patch(
            "/api/users/99999999", json=patch_body(current=PASSWORD, role="admin")
        ).status_code
        == 404
    )


# --- capability and authentication come first -------------------------------


def test_capability_is_checked_before_the_body_contract(target) -> None:
    """A non-administrator gets 403, never the 422 a missing password would raise."""
    assert target.client.post("/api/users", json=create_body(current=OMIT)).status_code == 403
    assert target.client.post("/api/users", json=create_body(current=PASSWORD)).status_code == 403
    assert (
        target.client.patch(
            f"/api/users/{target.user_id}", json=patch_body(role="admin")
        ).status_code
        == 403
    )
    assert (
        target.client.patch(
            f"/api/users/{target.user_id}", json=patch_body(current=PASSWORD, role="admin")
        ).status_code
        == 403
    )
    assert account_state(target, target.user_id)["role"] == "user"


def test_an_anonymous_request_is_refused(admin) -> None:
    from app.main import app

    with TestClient(app) as anonymous:
        assert (
            anonymous.post("/api/users", json=create_body(current=PASSWORD)).status_code == 401
        )
        assert (
            anonymous.patch(
                "/api/users/1", json=patch_body(current=PASSWORD, role="admin")
            ).status_code
            == 401
        )

    assert find_account(admin, CREATED_PREFIX) is None


# --- the reused budget and gate ---------------------------------------------


def test_the_budget_refuses_the_admin_endpoints_with_429(admin, reauth_limits) -> None:
    reauth_limits(failures=2, window=300)
    before = event_ids(admin)

    assert admin.client.post("/api/users", json=create_body(current=WRONG)).status_code == 400
    assert admin.client.post("/api/users", json=create_body(current=WRONG)).status_code == 400

    # The right password, and it is still refused: a spent budget is the answer.
    refused = admin.client.post("/api/users", json=create_body(current=PASSWORD))
    assert refused.status_code == 429, refused.text
    assert refused.json() == {"detail": LIMITED_DETAIL}
    assert refused.headers["retry-after"] == "300"
    assert refused.headers["cache-control"] == "no-store"
    assert find_account(admin, CREATED_PREFIX) is None
    # Two failures were recorded and the refusals wrote nothing of their own.
    assert len(new_events(admin, "reauth_failed", before)) == 2
    assert reauth_limiter().is_limited(str(admin.user_id)) is True


def test_the_budget_is_shared_across_the_guarded_endpoints(admin, reauth_limits) -> None:
    """Switching endpoint must not buy a fresh allowance."""
    reauth_limits(failures=1, window=300)

    spent = admin.client.post(
        "/api/auth/password", json={"current_password": WRONG, "new_password": ANOTHER_PASSWORD}
    )
    assert spent.status_code == 400

    refused = admin.client.post("/api/users", json=create_body(current=PASSWORD))
    assert refused.status_code == 429, refused.text


def test_a_successful_check_clears_the_budget(admin, reauth_limits) -> None:
    reauth_limits(failures=2, window=300)

    assert admin.client.post("/api/users", json=create_body(current=WRONG)).status_code == 400
    assert reauth_limiter().failure_count(str(admin.user_id)) == 1

    assert admin.client.post("/api/users", json=create_body(current=PASSWORD)).status_code == 201
    assert reauth_limiter().failure_count(str(admin.user_id)) == 0


def test_a_saturated_gate_refuses_without_verifying(admin, monkeypatch) -> None:
    """The same shared gate as login, and being refused costs no Argon2 at all."""
    from app.api import auth as auth_api

    calls: list[str] = []
    real = auth_api.verify_user_password

    def spy(user, password):
        calls.append(password)
        return real(user, password)

    monkeypatch.setattr(auth_api, "verify_user_password", spy)

    verifications = login_limiter()
    held = [verifications.gate.acquire() for _ in range(verifications.gate.limit)]
    assert all(held), "every slot must be taken for this test"
    try:
        refused = admin.client.post("/api/users", json=create_body(current=PASSWORD))
    finally:
        for _ in held:
            verifications.gate.release()

    assert refused.status_code == 429, refused.text
    assert refused.headers["retry-after"] == "1"
    assert calls == [], "a refused attempt must not spend a verification"
    assert verifications.gate.in_flight == 0
    assert find_account(admin, CREATED_PREFIX) is None


# --- cache, audit and secrets ----------------------------------------------


def test_admin_responses_are_never_cached(admin, target, reauth_limits) -> None:
    reauth_limits(failures=1, window=300)

    responses = {
        "list": admin.client.get("/api/users"),
        "created": admin.client.post("/api/users", json=create_body(current=PASSWORD)),
        "duplicate": admin.client.post("/api/users", json=create_body(current=PASSWORD)),
        "missing_password": admin.client.post("/api/users", json=create_body(current=OMIT)),
        "wrong_password": admin.client.patch(
            f"/api/users/{target.user_id}", json=patch_body(current=WRONG, role="admin")
        ),
        "limited": admin.client.post("/api/users", json=create_body(current=PASSWORD)),
        "forbidden": target.client.get("/api/users"),
    }

    assert [response.status_code for response in responses.values()] == [
        200,
        201,
        409,
        422,
        400,
        429,
        403,
    ], {name: response.status_code for name, response in responses.items()}
    for name, response in responses.items():
        assert response.headers.get("cache-control") == "no-store", name


def test_the_audit_names_the_actor_and_never_the_password_field(admin, target) -> None:
    before = event_ids(admin)

    response = admin.client.patch(
        f"/api/users/{target.user_id}", json=patch_body(current=PASSWORD, display_name=RENAMED)
    )
    assert response.status_code == 200

    recorded = new_events(admin, "user_updated", before)
    assert len(recorded) == 1
    assert recorded[0].entity_id == target.user_id
    # ``fields`` lists the *account* fields that changed: the password the caller
    # typed is not one of them and must not appear, not even as a name.
    assert recorded[0].payload == {"by": admin.username, "fields": ["display_name"]}
    assert PASSWORD not in str(recorded[0].payload)
    assert "current_password" not in str(recorded[0].payload)


def test_the_audit_of_a_created_account_names_the_actor(admin) -> None:
    before = event_ids(admin)

    assert admin.client.post("/api/users", json=create_body(current=PASSWORD)).status_code == 201

    recorded = new_events(admin, "user_created", before)
    assert len(recorded) == 1
    assert recorded[0].payload["by"] == admin.username
    assert PASSWORD not in str(recorded[0].payload)


def test_no_password_token_or_hash_reaches_a_response(admin, target) -> None:
    canary = "canary-must-never-be-echoed-1"

    created = admin.client.post("/api/users", json=create_body(current=PASSWORD, password=canary))
    listing = admin.client.get("/api/users")
    updated = admin.client.patch(
        f"/api/users/{target.user_id}", json=patch_body(current=PASSWORD, password=canary)
    )

    assert created.status_code == 201, created.text
    assert updated.status_code == 200, updated.text
    assert listing.status_code == 200

    token = admin.client.cookies.get(COOKIE_NAME)
    assert token, "the caller has to hold a token for this test to mean anything"
    with admin.session() as session:
        token_hash = session.scalar(
            select(UserSession.token_hash).where(UserSession.user_id == admin.user_id)
        )
        hashes = list(session.scalars(select(User.password_hash)).all())
    assert token_hash, "a session row has to exist for this test to mean anything"
    # The canary really was stored as a hash, so the "not in the response" checks below
    # are not passing by accident.
    assert any(verify_password(canary, digest) for digest in hashes)

    for response in (created, listing, updated):
        text = response.text
        assert canary not in text, "the submitted password must never be echoed"
        assert "password_hash" not in text
        assert token not in text
        assert token_hash not in text
