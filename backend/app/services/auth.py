from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User, UserSession, UserSettings
from app.security import UNUSABLE_PASSWORD, hash_password
from app.services.userdata import NOT_FOUND_SESSION, not_found

COOKIE_NAME = "shici_session"

#: 32 bytes of urandom, url-safe encoded. Passed to the client as the cookie
#: value; only its SHA-256 digest is persisted.
TOKEN_BYTES = 32

#: How stale ``last_seen_at`` may become before another request rewrites it.
#:
#: Every authenticated request passes through :func:`touch_session`, and SQLite
#: serialises writers, so writing on every request would turn each read into a
#: write transaction and contend for the database lock. The idle timeout is
#: measured in days, so recording "last seen" to the nearest few minutes keeps
#: the same meaning without a write per request.
TOUCH_INTERVAL = timedelta(minutes=5)


def normalize_username(username: str) -> str:
    return username.strip().casefold()


def create_user(
    session: Session,
    username: str,
    *,
    password: str | None = None,
    role: str = "user",
    display_name: str = "",
) -> User:
    """Create a user. Without a password the account cannot be logged into."""
    normalized = normalize_username(username)
    if not normalized:
        raise ValueError("用户名不能为空")
    existing = session.scalar(select(User).where(User.username == normalized))
    if existing is not None:
        raise ValueError(f"用户名已存在：{normalized}")
    user = User(
        username=normalized,
        display_name=display_name or normalized,
        role=role,
        password_hash=hash_password(password) if password else UNUSABLE_PASSWORD,
    )
    session.add(user)
    session.flush()
    session.add(UserSettings(user_id=user.id))
    session.commit()
    session.refresh(user)
    return user


def set_password(session: Session, user: User, password: str) -> User:
    user.password_hash = hash_password(password)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def generate_session_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(
    session: Session, user: User, *, user_agent: str = "", now: datetime | None = None
) -> tuple[str, UserSession]:
    """Create a session row and return the raw token for the cookie."""
    issued = now or datetime.now(UTC)
    token = generate_session_token()
    record = UserSession(
        user_id=user.id,
        token_hash=hash_session_token(token),
        created_at=issued,
        expires_at=issued + timedelta(days=get_settings().session_days),
        user_agent=user_agent[:300],
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return token, record


def _as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; every value in this table is UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def session_idle_limit() -> timedelta | None:
    """The configured idle timeout, or ``None`` when the idle check is off."""
    days = get_settings().session_idle_days
    return timedelta(days=days) if days > 0 else None


def last_activity_at(record: UserSession) -> datetime:
    """When this session was last used, falling back to when it was created.

    A freshly issued session has no ``last_seen_at`` yet, so creation is the only
    honest starting point for the idle clock -- otherwise a session nobody ever
    used would count as idle for ever.
    """
    return _as_utc(record.last_seen_at or record.created_at)


def session_is_live(record: UserSession, *, now: datetime | None = None) -> bool:
    """The single definition of a usable session.

    Two independent limits, both expressed over columns that already exist:

    * **absolute** -- ``expires_at`` is fixed when the session is issued and is
      never extended, so no amount of activity can keep a session past
      ``session_days``;
    * **idle** -- ``VOCAB_SESSION_IDLE_DAYS`` (default 7, ``0`` disables) measured
      from the last use, so an abandoned session stops working long before its
      absolute deadline.

    A revoked session is never live. :func:`prune_sessions` deletes exactly the
    rows this predicate rejects, which is why both live here: a cleanup that used
    its own rule could delete a session the API would still have accepted, or keep
    one for ever.
    """
    moment = now or datetime.now(UTC)
    if record.revoked_at is not None:
        return False
    if _as_utc(record.expires_at) <= moment:
        return False
    idle = session_idle_limit()
    if idle is None:
        return True
    # Strictly inside the window: at the boundary the session is already dead, and
    # prune_sessions deletes that same boundary, so the two cannot disagree.
    return last_activity_at(record) > moment - idle


def find_session_record(session: Session, token: str) -> UserSession | None:
    """The session row for a raw token, live or not.

    Only logout uses this: signing out has to be able to clear a cookie whose
    session is already revoked or expired, which :func:`resolve_session` refuses to
    return by design. Finding a row is *not* an authentication check -- a token
    that matches a row still says nothing about whether that session may be used --
    so nothing that decides who the caller is may call this.
    """
    if not token:
        return None
    return session.scalar(
        select(UserSession).where(UserSession.token_hash == hash_session_token(token))
    )


def resolve_session(
    session: Session, token: str, *, now: datetime | None = None
) -> UserSession | None:
    """Return the live session for a raw token, or None.

    Read-only on purpose: presenting a dead token must not write anything. An idle
    session is simply not live any more, and :func:`prune_sessions` is what removes
    the row.
    """
    record = find_session_record(session, token)
    if record is None or not session_is_live(record, now=now):
        return None
    return record


def revoke_session(
    session: Session, record: UserSession, now: datetime | None = None
) -> bool:
    """Revoke a session. Returns True when this call is what revoked it.

    Idempotent: revoking an already revoked session keeps the original timestamp.
    The first logout is the fact worth recording, and a repeated one -- which is
    exactly what an idempotent logout invites -- must not rewrite it.
    """
    if record.revoked_at is not None:
        return False
    record.revoked_at = now or datetime.now(UTC)
    session.add(record)
    session.commit()
    return True


def touch_session(
    session: Session, record: UserSession, now: datetime | None = None
) -> bool:
    """Record that this session was just used.

    Only ``last_seen_at`` changes: the token, the row and ``expires_at`` are left
    alone, so touching can never extend a session's absolute lifetime. Returns
    ``True`` when a write actually happened; a session touched less than
    :data:`TOUCH_INTERVAL` ago is left untouched (see that constant).
    """
    moment = now or datetime.now(UTC)
    previous = record.last_seen_at
    if previous is not None and moment - _as_utc(previous) < TOUCH_INTERVAL:
        return False
    record.last_seen_at = moment
    session.add(record)
    session.commit()
    return True


def prune_sessions(session: Session, now: datetime | None = None) -> int:
    """Delete every session that can never be used again, and report how many.

    The deleted set is the exact complement of :func:`session_is_live`: revoked
    rows, rows past their absolute ``expires_at``, and rows idle past
    ``VOCAB_SESSION_IDLE_DAYS``. A session that is still valid is never touched --
    including one that is simply logged in and unused, which is *not* idle until
    the configured span has passed since it was created.

    Deleting a dead row is safe in a way that deleting a live one would not be:
    ``last_seen_at`` is only written by :func:`touch_session`, which runs only
    after :func:`session_is_live` accepted the session, so a row this predicate
    rejects can never be brought back to life by a later request. The worst case
    of a request arriving in the same instant the row is deleted is that the caller
    logs in again.
    """
    moment = now or datetime.now(UTC)
    dead = [UserSession.revoked_at.is_not(None), UserSession.expires_at <= moment]
    idle = session_idle_limit()
    if idle is not None:
        cutoff = moment - idle
        # The exact complement of the idle half of ``session_is_live``, written
        # NULL-safely: a session is dead once its last use -- or its creation, when
        # it was never used -- is at or before the cutoff.
        #
        # Do NOT rewrite this as ``NOT (still_used_recently)``. ``last_seen_at`` is
        # NULL until the first request, and in SQL ``NULL > cutoff`` is NULL, so the
        # negation is NULL too and the row would never be selected: the one session
        # state every account starts in would leak for ever. The two branches below
        # guard on IS NULL / IS NOT NULL, so nothing propagates NULL.
        dead.append(
            or_(
                and_(
                    UserSession.last_seen_at.is_not(None),
                    UserSession.last_seen_at <= cutoff,
                ),
                and_(
                    UserSession.last_seen_at.is_(None),
                    UserSession.created_at <= cutoff,
                ),
            )
        )
    result = session.execute(
        delete(UserSession).where(or_(*dead)),
        # The predicate is evaluated in SQL only. Letting the ORM also evaluate it
        # in Python over any session already loaded in this Session raises
        # "can't compare offset-naive and offset-aware datetimes" (SQLite hands
        # back naive values). Cleanup must not depend on what the caller happens to
        # have in memory, so the objects are expired instead.
        execution_options={"synchronize_session": False},
    )
    session.expire_all()
    session.commit()
    return int(result.rowcount or 0)


def list_user_sessions(
    session: Session, user: User, *, now: datetime | None = None
) -> list[UserSession]:
    """This user's live sessions, most recently active first.

    Liveness is decided by :func:`session_is_live` rather than by a second SQL
    predicate: the list has to agree with the check every request goes through, and
    two definitions of "usable" is exactly how they drift apart. A user has a
    handful of sessions, and dead rows are removed by :func:`prune_sessions`, so
    filtering here costs nothing worth optimising.

    Ordering is ``COALESCE(last_seen_at, created_at) DESC``: a session that has never
    been used falls back to when it was issued, which is the honest answer.
    """
    moment = now or datetime.now(UTC)
    records = session.scalars(
        select(UserSession)
        .where(UserSession.user_id == user.id)
        .order_by(
            func.coalesce(UserSession.last_seen_at, UserSession.created_at).desc(),
            UserSession.id.desc(),
        )
    ).all()
    return [record for record in records if session_is_live(record, now=moment)]


def revoke_user_session(
    session: Session, user: User, session_id: int, now: datetime | None = None
) -> tuple[UserSession, bool]:
    """Revoke one of this user's sessions, or raise ``NotFoundError``.

    The lookup is scoped by ``user_id`` in SQL, so another user's id and an id that
    does not exist produce the same refusal: a probe cannot tell "not yours" from
    "not there".

    Returns the row and whether *this* call is what revoked it, so the caller can
    record an audit event for a real revocation without one for a repeat request.
    Revoking is idempotent and keeps the original revocation time.
    """
    record = session.scalar(
        select(UserSession).where(
            UserSession.id == session_id, UserSession.user_id == user.id
        )
    )
    if record is None:
        raise not_found(NOT_FOUND_SESSION)
    changed = revoke_session(session, record, now)
    return record, changed


def revoke_all_sessions(session: Session, user: User, now: datetime | None = None) -> int:
    moment = now or datetime.now(UTC)
    records = session.scalars(
        select(UserSession).where(
            UserSession.user_id == user.id, UserSession.revoked_at.is_(None)
        )
    ).all()
    for record in records:
        record.revoked_at = moment
        session.add(record)
    session.commit()
    return len(records)
