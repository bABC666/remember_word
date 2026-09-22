from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User, UserSession, UserSettings
from app.security import UNUSABLE_PASSWORD, hash_password

COOKIE_NAME = "shici_session"

#: 32 bytes of urandom, url-safe encoded. Passed to the client as the cookie
#: value; only its SHA-256 digest is persisted.
TOKEN_BYTES = 32


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


def resolve_session(session: Session, token: str) -> UserSession | None:
    """Return the live session for a raw token, or None."""
    if not token:
        return None
    record = session.scalar(
        select(UserSession).where(UserSession.token_hash == hash_session_token(token))
    )
    if record is None or record.revoked_at is not None:
        return None
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        return None
    return record


def revoke_session(session: Session, record: UserSession, now: datetime | None = None) -> None:
    record.revoked_at = now or datetime.now(UTC)
    session.add(record)
    session.commit()


def touch_session(session: Session, record: UserSession, now: datetime | None = None) -> None:
    record.last_seen_at = now or datetime.now(UTC)
    session.add(record)
    session.commit()


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
