"""Shared FastAPI dependencies.

Ownership always comes from the session cookie, never from a client supplied
identifier: no endpoint may accept a ``user_id`` that decides whose data is read
or written.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import User, UserSettings
from app.services.auth import resolve_session

SessionDep = Annotated[Session, Depends(get_session)]


def _unauthorized(detail: str = "请先登录") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Cookie"},
    )


def get_current_user(
    session: SessionDep,
    shici_session: Annotated[str | None, Cookie()] = None,
) -> User:
    record = resolve_session(session, shici_session or "")
    if record is None:
        raise _unauthorized()
    user = session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise _unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    # 403 is correct here: the caller is authenticated, the capability is not
    # theirs. Resource-level ownership failures use 404 instead.
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def ensure_user_settings(session: Session, user: User) -> UserSettings:
    """Return the user's settings row, creating a default one on first use."""
    record = session.get(UserSettings, user.id)
    if record is None:
        record = UserSettings(user_id=user.id)
        session.add(record)
        session.commit()
        session.refresh(record)
    return record


def get_user_settings(session: SessionDep, user: CurrentUser) -> UserSettings:
    return ensure_user_settings(session, user)


def find_user_by_username(session: Session, username: str) -> User | None:
    normalized = username.strip().casefold()
    if not normalized:
        return None
    return session.scalar(select(User).where(User.username == normalized))
