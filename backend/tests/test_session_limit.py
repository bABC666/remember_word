"""G5: how many sessions one account may hold (``VOCAB_MAX_SESSIONS_PER_USER``).

Design: ``docs/V1.2-PHASE2.7-A-SESSION-MANAGEMENT-DESIGN.md`` §5.2 / §7.1 (2.7-e).
Before this, a session could only be created with a valid password but there was no
upper bound at all, so every login added a row for ever.

The rules these tests pin, in the words the design uses:

* the limit applies to the **live** sessions of one account, defaults to 10, and
  ``0`` switches it off;
* going over it **never refuses the new login** -- refusing would deadlock exactly
  the user who lost a device and needs to sign in to revoke it. Instead the least
  recently active session is revoked;
* "least recently active" means ``COALESCE(last_seen_at, created_at)`` earliest, so a
  session created long ago but used a minute ago outlives a newer idle one; ties go
  to the oldest row, which keeps the choice deterministic;
* the session **this request just issued is never the one evicted** -- a hard
  constraint, not a consequence of the ordering;
* dead rows (revoked, expired, idle past the window) are cleaned up first, since
  they cost a row and can never be used again;
* one account's overflow never touches another account's sessions;
* every eviction leaves an audit event carrying a fixed reason and no secret.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.models import HistoryEvent, UserSession
from app.services.auth import COOKIE_NAME, create_session, session_is_live

PASSWORD = "test-password-123"

#: The documented default (design §5.2).
DEFAULT_LIMIT = 10


def set_limit(monkeypatch, value: int | str) -> None:
    """Apply the setting and drop the cached settings, as a restart would."""
    monkeypatch.setenv("VOCAB_MAX_SESSIONS_PER_USER", str(value))
    get_settings.cache_clear()


def add_session(
    world,
    *,
    created_at: datetime | None = None,
    last_seen_at: datetime | None = None,
    revoked: bool = False,
) -> tuple[str, int]:
    """Create one more session for this user, optionally already dead."""
    with world.session() as session:
        token, record = create_session(session, world.reload_user(), now=created_at)
        if last_seen_at is not None:
            record.last_seen_at = last_seen_at
        if revoked:
            record.revoked_at = datetime.now(UTC)
        session.add(record)
        session.commit()
        return token, record.id


def login(world, password: str = PASSWORD):
    """Sign in through the API. Success is the requirement, so callers assert it."""
    return world.client.post(
        "/api/auth/login", json={"username": world.username, "password": password}
    )


def use_token(world, token: str):
    """Present a specific session token, the way another device would."""
    world.client.cookies.clear()
    world.client.cookies.set(COOKIE_NAME, token)
    return world.client.get("/api/auth/me")


def rows_for(world, *, live_only: bool) -> list[UserSession]:
    with world.session() as session:
        rows = list(
            session.scalars(
                select(UserSession)
                .where(UserSession.user_id == world.user_id)
                .order_by(UserSession.id)
            ).all()
        )
    if not live_only:
        return rows
    return [row for row in rows if session_is_live(row)]


def live_ids(world) -> set[int]:
    return {row.id for row in rows_for(world, live_only=True)}


def event_ids(world) -> set[int]:
    with world.session() as session:
        return set(session.scalars(select(HistoryEvent.id)).all())


def new_events(world, event_type: str, before: set[int]) -> list[HistoryEvent]:
    with world.session() as session:
        events = list(
            session.scalars(
                select(HistoryEvent)
                .where(HistoryEvent.event_type == event_type)
                .order_by(HistoryEvent.id)
            ).all()
        )
    return [event for event in events if event.id not in before]


# --- the setting -----------------------------------------------------------


def test_the_default_limit_is_ten() -> None:
    assert get_settings().max_sessions_per_user == DEFAULT_LIMIT


def test_the_limit_comes_from_the_environment(monkeypatch) -> None:
    set_limit(monkeypatch, 3)
    assert get_settings().max_sessions_per_user == 3
    set_limit(monkeypatch, 0)
    assert get_settings().max_sessions_per_user == 0, "0 means no limit"


def test_a_nonsense_or_negative_value_is_treated_as_off(monkeypatch) -> None:
    set_limit(monkeypatch, "not-a-number")
    assert get_settings().max_sessions_per_user == DEFAULT_LIMIT
    set_limit(monkeypatch, -4)
    assert get_settings().max_sessions_per_user == 0


# --- staying within the limit ---------------------------------------------


def test_a_login_under_the_limit_signs_nobody_out(world, monkeypatch) -> None:
    set_limit(monkeypatch, 3)
    before = event_ids(world)

    for _ in range(2):
        assert login(world).status_code == 200

    assert len(live_ids(world)) == 3
    assert new_events(world, "session_evicted", before) == []


def test_a_login_at_the_limit_evicts_instead_of_being_refused(world, monkeypatch) -> None:
    set_limit(monkeypatch, 3)
    for _ in range(2):
        assert login(world).status_code == 200
    assert len(live_ids(world)) == 3

    response = login(world)

    assert response.status_code == 200, response.text
    assert len(live_ids(world)) == 3, "the account ends at the limit, not above it"


def test_the_least_recently_active_session_is_the_one_that_goes(world, monkeypatch) -> None:
    # Built with the cap off, then lowered: creating a session is itself subject to
    # the cap, so a fixture that sets one up under an active cap would be enforcing
    # the limit while arranging the very state it wants to observe.
    set_limit(monkeypatch, 0)
    now = datetime.now(UTC)
    stale_token, stale_id = add_session(world, created_at=now - timedelta(hours=3))
    fresh_token, fresh_id = add_session(world, created_at=now - timedelta(hours=1))
    set_limit(monkeypatch, 3)

    assert login(world).status_code == 200

    surviving = live_ids(world)
    assert stale_id not in surviving, "the session idle longest is the one evicted"
    assert fresh_id in surviving
    assert use_token(world, stale_token).status_code == 401
    assert use_token(world, fresh_token).status_code == 200


def test_activity_counts_before_creation(world, monkeypatch) -> None:
    """``COALESCE(last_seen_at, created_at)``: the rule is last use, not age."""
    set_limit(monkeypatch, 0)
    now = datetime.now(UTC)
    _old_token, old_but_active = add_session(
        world, created_at=now - timedelta(days=2), last_seen_at=now - timedelta(minutes=10)
    )
    _new_token, newer_but_stale = add_session(
        world, created_at=now - timedelta(hours=6), last_seen_at=now - timedelta(hours=5)
    )
    set_limit(monkeypatch, 3)

    assert login(world).status_code == 200

    surviving = live_ids(world)
    assert newer_but_stale not in surviving
    assert old_but_active in surviving, "a two-day-old session used ten minutes ago survives"


def test_a_tie_on_activity_time_evicts_the_oldest_row(world, monkeypatch) -> None:
    """Deterministic tie-break: same activity time, the smaller id goes first."""
    set_limit(monkeypatch, 0)
    same_moment = datetime.now(UTC) - timedelta(hours=2)
    _first_token, first_id = add_session(world, created_at=same_moment)
    _second_token, second_id = add_session(world, created_at=same_moment)
    set_limit(monkeypatch, 3)
    assert first_id < second_id

    assert login(world).status_code == 200

    surviving = live_ids(world)
    assert first_id not in surviving
    assert second_id in surviving


def test_the_session_just_issued_is_never_evicted(world, monkeypatch) -> None:
    """Limit 1 is the extreme case: the new session must survive its own enforcement."""
    set_limit(monkeypatch, 1)

    assert login(world).status_code == 200

    assert len(live_ids(world)) == 1
    assert world.client.get("/api/auth/me").status_code == 200


def test_dead_sessions_are_cleaned_up_before_a_live_one_is_evicted(world, monkeypatch) -> None:
    set_limit(monkeypatch, 0)
    now = datetime.now(UTC)
    _live_token, oldest_live = add_session(world, created_at=now - timedelta(hours=4))
    _dead_token, dead_id = add_session(
        world, created_at=now - timedelta(hours=3), revoked=True
    )
    set_limit(monkeypatch, 2)

    assert login(world).status_code == 200

    remaining = {row.id for row in rows_for(world, live_only=False)}
    assert dead_id not in remaining, "a dead row is removed, never counted or evicted"
    assert oldest_live not in live_ids(world), "the oldest live session still goes"
    assert len(live_ids(world)) == 2


def test_the_limit_can_be_switched_off(world, monkeypatch) -> None:
    set_limit(monkeypatch, 0)
    before = event_ids(world)

    for _ in range(DEFAULT_LIMIT + 2):
        assert login(world).status_code == 200

    assert len(live_ids(world)) == DEFAULT_LIMIT + 3, "the fixture's own session plus twelve"
    assert new_events(world, "session_evicted", before) == []


def test_lowering_the_limit_takes_effect_on_the_next_login(world, monkeypatch) -> None:
    """The cap is read from rows, so no restart and no in-memory state is involved."""
    set_limit(monkeypatch, 0)
    for _ in range(4):
        assert login(world).status_code == 200
    assert len(live_ids(world)) == 5

    set_limit(monkeypatch, 2)
    before = event_ids(world)

    assert login(world).status_code == 200

    assert len(live_ids(world)) == 2
    assert len(new_events(world, "session_evicted", before)) == 4, "one event per eviction"


def test_one_accounts_overflow_leaves_another_account_alone(two_worlds, monkeypatch) -> None:
    set_limit(monkeypatch, 2)
    a, b = two_worlds
    _a_token, a_extra = add_session(a)
    _b_token, b_extra = add_session(b)
    b_before = live_ids(b)
    assert a_extra in live_ids(a) and b_extra in b_before

    assert login(a).status_code == 200

    assert len(live_ids(a)) == 2
    assert live_ids(b) == b_before, "nothing of B's changed"
    assert {row.id for row in rows_for(b, live_only=False)} == b_before


# --- what an eviction leaves behind ---------------------------------------


def test_the_evicted_session_is_revoked_rather_than_deleted(world, monkeypatch) -> None:
    set_limit(monkeypatch, 1)
    with world.session() as session:
        fixture_id = session.scalar(
            select(UserSession.id).where(UserSession.user_id == world.user_id)
        )
    assert fixture_id is not None

    assert login(world).status_code == 200

    with world.session() as session:
        row = session.get(UserSession, fixture_id)
    assert row is not None, "the row stays in the table until prune_sessions removes it"
    assert row.revoked_at is not None, "eviction is a revocation, not a silent delete"


def test_an_eviction_is_audited_without_any_secret(world, monkeypatch) -> None:
    set_limit(monkeypatch, 1)
    evicted_token = world.client.cookies.get(COOKIE_NAME)
    assert evicted_token
    with world.session() as session:
        evicted_id = session.scalar(
            select(UserSession.id).where(UserSession.user_id == world.user_id)
        )
    before = event_ids(world)

    assert login(world).status_code == 200

    recorded = new_events(world, "session_evicted", before)
    assert len(recorded) == 1
    event = recorded[0]
    assert event.user_id == world.user_id, "the event belongs to the account, not to the session"
    assert event.entity_type == "user_session"
    assert event.entity_id == evicted_id
    # A fixed reason, chosen by the server: nothing the caller sent can reach here.
    assert event.payload == {"reason": "session_limit", "limit": 1}

    rendered = f"{event.payload}{event.entity_type}{event.entity_id}"
    assert evicted_token not in rendered, "the raw token must never be stored"
    with world.session() as session:
        token_hash = session.scalar(
            select(UserSession.token_hash).where(UserSession.id == evicted_id)
        )
    assert token_hash and token_hash not in rendered, "the verifier must never be stored"


def test_eviction_does_not_change_the_login_response(world, monkeypatch) -> None:
    set_limit(monkeypatch, 0)
    unlimited = login(world)
    assert unlimited.status_code == 200

    set_limit(monkeypatch, 1)
    limited = login(world)

    assert limited.status_code == 200
    assert set(limited.json()) == set(unlimited.json()), "eviction adds no field to the answer"
    assert limited.json()["username"] == unlimited.json()["username"]
