from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select

from app.api.deps import (
    AdminUser,
    CurrentUser,
    SessionDep,
    ensure_user_settings,
    find_user_by_username,
)
from app.config import get_settings
from app.models import HistoryEvent, User, UserSettings
from app.schemas import (
    ChangePasswordRequest,
    CreateUserRequest,
    LoginRequest,
    UpdateUserRequest,
)
from app.security import hash_password, password_is_usable, verify_password
from app.services.auth import (
    COOKIE_NAME,
    create_session,
    normalize_username,
    resolve_session,
    revoke_all_sessions,
    revoke_session,
    set_password,
)

router = APIRouter(tags=["auth"])

#: Deliberately identical for "no such user" and "wrong password" so the
#: endpoint cannot be used to enumerate accounts.
INVALID_CREDENTIALS = "用户名或密码不正确"


def user_payload(user: User, settings: UserSettings) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "is_admin": user.is_admin,
        "settings": {
            "daily_new_words": settings.daily_new_words,
            "article_length": settings.article_length,
            "onboarding_seen": settings.onboarding_seen,
        },
    }


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=settings.session_days * 24 * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/api/auth/login")
def login(
    payload: LoginRequest, request: Request, response: Response, session: SessionDep
) -> dict[str, object]:
    user = find_user_by_username(session, payload.username)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)
    if not password_is_usable(user.password_hash):
        # Bootstrap accounts keep the "!" sentinel until an operator runs the
        # CLI, so they must never accept a login.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "该账号尚未设置密码，请在服务器上运行 python -m app.cli set-password "
            f"{user.username}",
        )
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)

    token, _record = create_session(
        session, user, user_agent=request.headers.get("user-agent", "")
    )
    _set_session_cookie(response, token)
    session.add(
        HistoryEvent(
            event_type="user_login",
            entity_type="user",
            entity_id=user.id,
            payload={"username": user.username},
        )
    )
    session.commit()
    return user_payload(user, ensure_user_settings(session, user))


@router.post("/api/auth/logout")
def logout(
    request: Request, response: Response, session: SessionDep, user: CurrentUser
) -> dict[str, bool]:
    record = resolve_session(session, request.cookies.get(COOKIE_NAME, ""))
    if record is not None and record.user_id == user.id:
        revoke_session(session, record)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/api/auth/me")
def read_me(session: SessionDep, user: CurrentUser) -> dict[str, object]:
    return user_payload(user, ensure_user_settings(session, user))


@router.post("/api/auth/password")
def change_password(
    payload: ChangePasswordRequest, session: SessionDep, user: CurrentUser
) -> dict[str, bool]:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "当前密码不正确")
    if payload.current_password == payload.new_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "新密码不能与当前密码相同")
    set_password(session, user, payload.new_password)
    # Changing a password invalidates every other session.
    revoke_all_sessions(session, user)
    session.add(
        HistoryEvent(
            event_type="user_password_changed",
            entity_type="user",
            entity_id=user.id,
            payload={},
        )
    )
    session.commit()
    return {"ok": True}


# --- admin only -----------------------------------------------------------


@router.get("/api/users")
def list_users(session: SessionDep, _admin: AdminUser) -> list[dict[str, object]]:
    users = session.scalars(select(User).order_by(User.id)).all()
    return [
        {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
            "is_active": user.is_active,
            "password_configured": password_is_usable(user.password_hash),
            "created_at": user.created_at,
        }
        for user in users
    ]


@router.post("/api/users", status_code=status.HTTP_201_CREATED)
def create_user_endpoint(
    payload: CreateUserRequest, session: SessionDep, admin: AdminUser
) -> dict[str, object]:
    normalized = normalize_username(payload.username)
    if find_user_by_username(session, normalized) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"用户名已存在：{normalized}")
    user = User(
        username=normalized,
        display_name=payload.display_name or normalized,
        role=payload.role,
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    session.flush()
    session.add(UserSettings(user_id=user.id))
    session.add(
        HistoryEvent(
            event_type="user_created",
            entity_type="user",
            entity_id=user.id,
            payload={"by": admin.username, "role": payload.role},
        )
    )
    session.commit()
    session.refresh(user)
    return user_payload(user, ensure_user_settings(session, user))


@router.patch("/api/users/{user_id}")
def update_user_endpoint(
    user_id: int, payload: UpdateUserRequest, session: SessionDep, admin: AdminUser
) -> dict[str, object]:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    changes = payload.model_dump(exclude_unset=True)
    if user.id == admin.id:
        if changes.get("is_active") is False:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能停用当前登录的管理员")
        if changes.get("role") == "user":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能移除自己的管理员权限")
    if "display_name" in changes and changes["display_name"] is not None:
        user.display_name = changes["display_name"]
    if "role" in changes and changes["role"] is not None:
        user.role = changes["role"]
    if "is_active" in changes and changes["is_active"] is not None:
        user.is_active = changes["is_active"]
        if not user.is_active:
            revoke_all_sessions(session, user)
    if changes.get("password"):
        set_password(session, user, changes["password"])
        revoke_all_sessions(session, user)
    session.add(user)
    session.add(
        HistoryEvent(
            event_type="user_updated",
            entity_type="user",
            entity_id=user.id,
            payload={"by": admin.username, "fields": sorted(changes)},
        )
    )
    session.commit()
    session.refresh(user)
    return user_payload(user, ensure_user_settings(session, user))
