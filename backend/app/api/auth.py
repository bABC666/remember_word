from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select

from app.api.deps import (
    AdminUser,
    CurrentSession,
    CurrentUser,
    SessionDep,
    ensure_user_settings,
    find_user_by_username,
)
from app.config import get_settings
from app.models import HistoryEvent, User, UserSession, UserSettings
from app.schemas import (
    ChangePasswordRequest,
    CreateUserRequest,
    LoginRequest,
    SessionDeleteRequest,
    SessionRevokeRequest,
    UpdateUserRequest,
)
from app.security import hash_password, password_is_usable
from app.services.auth import (
    COOKIE_NAME,
    create_session,
    find_session_record,
    list_user_sessions,
    normalize_username,
    revoke_all_sessions,
    revoke_all_user_sessions,
    revoke_other_sessions,
    revoke_session,
    revoke_user_session,
    set_password,
    verify_unknown_user_password,
    verify_user_password,
)
from app.services.limiter import login_limiter, reauth_limiter

router = APIRouter(tags=["auth"])

#: Deliberately identical for "no such user" and "wrong password" so the
#: endpoint cannot be used to enumerate accounts.
INVALID_CREDENTIALS = "用户名或密码不正确"

#: Advertised when the concurrency gate is saturated. Verification slots turn over
#: in well under a second, so this is a "come back shortly", not a lockout.
BUSY_RETRY_AFTER_SECONDS = 1

#: Which sensitive operation a re-auth attempt belonged to. A closed set: the value
#: ends up in an audit payload, so it must never be caller-supplied text.
ReauthAction = Literal[
    "change_password",
    "revoke_sessions",
    "revoke_session",
    "create_user",
    "update_user",
]
CHANGE_PASSWORD_ACTION: ReauthAction = "change_password"
REVOKE_SESSIONS_ACTION: ReauthAction = "revoke_sessions"
REVOKE_SESSION_ACTION: ReauthAction = "revoke_session"
CREATE_USER_ACTION: ReauthAction = "create_user"
UPDATE_USER_ACTION: ReauthAction = "update_user"


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


def _invalid_credentials() -> HTTPException:
    """The one refusal every failed login gets, whatever went wrong.

    Whether the username exists, is disabled, or has no password yet is not the
    caller's business: answering any of those differently -- in the body or in how
    long the answer takes -- is an account enumeration oracle. An operator can
    still see which accounts lack a password through ``GET /api/users`` (the
    ``password_configured`` field) and ``python -m app.cli list-users``, so nothing
    is lost by keeping the public answer uniform.
    """
    return HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)


def _too_many_login_attempts(retry_after: int) -> HTTPException:
    """The one refusal the abuse guards give, whichever guard fired.

    One message for both guards, and a message that says nothing about the
    username: being refused must not become a way to learn which usernames are
    real. ``Retry-After`` is the only detail, and it is about time, not identity.
    """
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="登录尝试过于频繁，请稍后再试",
        headers={"Retry-After": str(max(1, retry_after))},
    )


def _refuse_login(session: SessionDep, client: str, user: User | None) -> HTTPException:
    """Both records of a refused login, then the refusal itself.

    The audit event and the per-address counter are written together so a failure
    can never be counted without being recorded, and the refusal is returned rather
    than raised so every caller ends in the same ``raise`` line.
    """
    _record_login_failure(session, user)
    login_limiter().failures.record_failure(client)
    return _invalid_credentials()


def _too_many_reauth_attempts(retry_after: int) -> HTTPException:
    """The refusal for a sensitive operation whose password check may not run.

    Wording differs from the login refusal on purpose -- the user is signed in and
    acting in their own settings, so "登录尝试" would be misleading -- but the shape
    is the same 429 with ``Retry-After``, and like the login one it says nothing
    about the account.
    """
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="密码校验尝试过于频繁，请稍后再试",
        headers={"Retry-After": str(max(1, retry_after))},
    )


def _require_password(
    session: SessionDep, user: User, password: str, *, action: ReauthAction
) -> None:
    """Gate a sensitive operation behind the account's own password.

    The order of the four steps is the design:

    1. **The budget is checked first, before the gate.** An attempt that is going to
       be refused must not occupy a verification slot, and must not cost a
       verification either -- otherwise a refused attempt is still a way to spend the
       server's CPU.
    2. **One verification, inside the shared gate.** :func:`verify_user_password`
       always spends exactly one Argon2, and the gate bounds how many run at once
       across the whole process, so this endpoint cannot be used to exhaust memory
       through a path the login limiter does not watch.
    3. **A failure is recorded twice**: an audit event attributed to the account, and
       one failure in that account's budget. The response is the same 400 the
       endpoint returned before this existed, so nothing new is disclosed.
    4. **A success clears the budget**, so a user who fumbles and then gets it right
       is not left one attempt from a refusal.

    The 429 paths write nothing: a refusal is not an event worth storing, and storing
    it would make the limiter itself a write path for an authenticated caller.

    ``action`` is a closed set of literals rather than a free string, so no
    caller-supplied text can ever reach an audit payload.
    """
    budget = reauth_limiter()
    key = str(user.id)

    if budget.is_limited(key):
        raise _too_many_reauth_attempts(budget.retry_after(key))

    verifications = login_limiter()
    if not verifications.gate.acquire():
        raise _too_many_reauth_attempts(BUSY_RETRY_AFTER_SECONDS)
    try:
        matched = verify_user_password(user, password)
    finally:
        # Released on every path, including an exception raised inside the check.
        verifications.gate.release()

    if not matched:
        session.add(
            HistoryEvent(
                user_id=user.id,
                event_type="reauth_failed",
                entity_type="user",
                entity_id=user.id,
                payload={"action": action},
            )
        )
        session.commit()
        budget.record_failure(key)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "当前密码不正确")

    budget.clear(key)


def _session_cookie_attributes() -> dict[str, object]:
    """The attributes the session cookie is issued with, as one definition.

    Deleting a cookie has to repeat the attributes it was set with (name, path,
    secure, httponly, samesite): browsers match on name and path, and a later move
    to a ``__Host-``/``__Secure-`` prefixed name would silently stop deleting if
    the deletion omitted ``Secure``. Sharing the values is what keeps the set and
    the delete from drifting apart.
    """
    return {
        "path": "/",
        "secure": get_settings().cookie_secure,
        "httponly": True,
        "samesite": "lax",
    }


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=settings.session_days * 24 * 3600,
        **_session_cookie_attributes(),
    )


def _record_login_failure(session: SessionDep, user: User | None) -> None:
    """Record a refused login attempt without recording anything unverified.

    Two shapes, on purpose:

    * the username resolved to an account, so the event is attributed to it
      (``user_id``): that is what makes an attack on a real account visible, and it
      stores only the name already held on the account row;
    * the username resolved to nothing, so the event carries no subject and no
      payload. The attempted string is attacker-controlled -- a guess, an address,
      junk -- and is therefore never written down, while the row itself still counts
      enumeration attempts that would otherwise leave no trace at all.

    No password, and no token, is ever part of this or of any other event.
    """
    session.add(
        HistoryEvent(
            user_id=user.id if user is not None else None,
            event_type="login_failed",
            entity_type="user" if user is not None else "",
            entity_id=user.id if user is not None else None,
            payload={"username": user.username} if user is not None else {},
        )
    )
    session.commit()


@router.post("/api/auth/login")
def login(
    payload: LoginRequest, request: Request, response: Response, session: SessionDep
) -> dict[str, object]:
    """Sign in, or answer one indistinguishable 401.

    Every failing path performs exactly one password verification and returns the
    same status and body:

    * an unknown username is verified against :func:`dummy_password_hash`, whose
      result is discarded -- there is no account to authenticate, the work only
      exists so the response time matches a real verification;
    * an account whose hash is the ``!`` sentinel verifies against the same dummy
      hash and is refused by ``password_is_usable`` regardless of the outcome, so
      the sentinel can never be logged into;
    * a disabled account is verified like any other and then refused.

    No branch reports *why* it refused, and none of them issues a session. Each one
    does leave a ``login_failed`` audit event, for the same reason a success leaves
    a ``user_login`` one.

    Two abuse guards run first, both before any hashing so that being refused costs
    the server nothing:

    * the address has already used up its failures inside the window -> 429;
    * every verification slot is busy -> 429, without waiting for one (see
      :mod:`app.services.limiter`).

    The address check comes first because it can refuse without taking a slot, and a
    request that is going to be refused should not be able to occupy one.
    """
    client = request.client.host if request.client is not None else ""
    limiter = login_limiter()

    if limiter.failures.is_limited(client):
        raise _too_many_login_attempts(limiter.failures.retry_after(client))

    if not limiter.gate.acquire():
        raise _too_many_login_attempts(BUSY_RETRY_AFTER_SECONDS)
    try:
        user = find_user_by_username(session, payload.username)
        if user is None:
            verify_unknown_user_password(payload.password)
            raise _refuse_login(session, client, None)

        # One shared verification of the account's own password; the account-state
        # check that follows is authentication's business, not the verifier's.
        if not verify_user_password(user, payload.password) or not user.is_active:
            raise _refuse_login(session, client, user)

        token, _record = create_session(
            session, user, user_agent=request.headers.get("user-agent", "")
        )
        _set_session_cookie(response, token)
        session.add(
            HistoryEvent(
                # Attributed to the account, so a login can be found by user rather
                # than only by the name written inside the payload.
                user_id=user.id,
                event_type="user_login",
                entity_type="user",
                entity_id=user.id,
                payload={"username": user.username},
            )
        )
        session.commit()
        # Signing in successfully forgives this address its earlier failures.
        limiter.failures.clear(client)
        return user_payload(user, ensure_user_settings(session, user))
    finally:
        # Released on every path, including the refusals raised above.
        limiter.gate.release()


@router.post("/api/auth/logout")
def logout(request: Request, response: Response, session: SessionDep) -> dict[str, bool]:
    """End this session, in whatever state it is in.

    Signing out must always work and must always clear the cookie, so this depends
    on nothing about the session: an absent cookie, an unrecognised token, a revoked
    session and an expired one all answer exactly like a successful logout. A 401
    here would both leave a dead cookie in the browser and tell an unauthenticated
    caller something about the token it presented.

    The row is revoked when one exists, and the first revocation time is kept if
    this is a repeat call. No branch distinguishes the cases in the response.
    """
    record = find_session_record(session, request.cookies.get(COOKIE_NAME, ""))
    if record is not None:
        revoke_session(session, record)
    # Deleted with the same attributes it was set with, so the deletion keeps
    # working if the cookie ever moves behind a __Host-/__Secure- prefixed name.
    response.delete_cookie(COOKIE_NAME, **_session_cookie_attributes())
    return {"ok": True}


def session_dict(record: UserSession, *, current_session_id: int) -> dict[str, object]:
    """One session, as the caller's own device list needs it.

    The field list is the whole contract and it stops at metadata: the raw token
    exists only inside the HttpOnly cookie and ``token_hash`` is the verification
    material, so neither -- nor anything derived from them -- belongs in a response.
    """
    return {
        "id": record.id,
        "current": record.id == current_session_id,
        "created_at": record.created_at,
        "last_seen_at": record.last_seen_at,
        "expires_at": record.expires_at,
        "user_agent": record.user_agent,
    }


@router.get("/api/auth/sessions")
def list_sessions(
    session: SessionDep, user: CurrentUser, current: CurrentSession
) -> dict[str, object]:
    """The caller's own live sessions, most recently active first.

    Only rows whose ``user_id`` is the caller's are ever read, and dead ones (revoked,
    past their absolute expiry, or idle) are left out: they cannot be used and
    ``prune_sessions`` removes them anyway, so listing them would offer the user
    buttons that do nothing.
    """
    records = list_user_sessions(session, user)
    return {"sessions": [session_dict(record, current_session_id=current.id) for record in records]}


@router.delete("/api/auth/sessions/{session_id}")
def revoke_session_endpoint(
    payload: SessionDeleteRequest,
    session_id: int,
    response: Response,
    session: SessionDep,
    user: CurrentUser,
    current: CurrentSession,
) -> dict[str, bool]:
    """Revoke one of the caller's own sessions, behind the re-auth guard.

    The lookup is scoped by ``user_id``, and another user's session id answers 404
    exactly like an id that does not exist -- so this endpoint cannot be used to
    discover which session ids are real (the project's IDOR rule: 404, never 403).

    The order is the contract, as in bulk revocation: the password is checked
    **before** anything is revoked, because revoking first would leave a refused
    request with a real and unrecoverable effect.

    The password is required here for the same reason the bulk action requires it:
    the phase 2.7-d design rule is that anything which changes *the set of
    credentials that can reach the account* needs re-authentication (its section
    3.2), and a single device is exactly that. The upside is concrete -- a stolen
    cookie can no longer be used to kick the real owner's other devices offline.

    Revoking is idempotent: a repeat request is a success that changes nothing and
    writes no second audit event. When the session being revoked is the one making
    the request, the cookie is deleted too -- otherwise the caller would be left
    holding a dead cookie until the next request failed.
    """
    _require_password(session, user, payload.current_password, action=REVOKE_SESSION_ACTION)

    record, changed = revoke_user_session(session, user, session_id)
    if changed:
        # Only a revocation that actually happened is worth recording, so a repeat
        # request adds no second event.
        session.add(
            HistoryEvent(
                user_id=user.id,
                event_type="session_revoked",
                entity_type="user_session",
                entity_id=record.id,
                payload={"current": record.id == current.id},
            )
        )
        session.commit()
    if record.id == current.id:
        # Revoking the session making the request is a logout: clear the cookie with
        # the same attributes it was issued with.
        response.delete_cookie(COOKIE_NAME, **_session_cookie_attributes())
    return {"ok": True}


@router.post("/api/auth/sessions/revoke")
def revoke_sessions_endpoint(
    payload: SessionRevokeRequest,
    response: Response,
    session: SessionDep,
    user: CurrentUser,
    current: CurrentSession,
) -> dict[str, object]:
    """Sign out the account's other sessions, or every session including this one.

    The order is the contract and it is not negotiable:

    1. the session making the request is resolved (as a dependency, before any of
       this runs);
    2. the password is verified by the shared re-auth guard -- budget, shared
       verification gate, ``reauth_failed`` audit;
    3. only then is anything revoked;
    4. one ``session_revoked`` event is written per revoked session;
    5. and only ``scope="all"`` clears the cookie, because only that scope ends the
       caller's own session.

    Revoking first and checking the password afterwards would leave a failed request
    with a real, unrecoverable effect. Nothing else about the outcome is disclosed:
    the response says how many of *the caller's* sessions ended and nothing about
    which ids exist or who they belong to.
    """
    _require_password(session, user, payload.current_password, action=REVOKE_SESSIONS_ACTION)

    if payload.scope == "others":
        revoked = revoke_other_sessions(session, user, current.id)
    else:
        revoked = revoke_all_user_sessions(session, user)

    if revoked:
        # One event per session: history_event is append-only evidence, and a summary
        # row would lose which sessions actually ended.
        session.add_all(
            [
                HistoryEvent(
                    user_id=user.id,
                    event_type="session_revoked",
                    entity_type="user_session",
                    entity_id=record.id,
                    payload={"scope": payload.scope},
                )
                for record in revoked
            ]
        )
        session.commit()

    if payload.scope == "all":
        # The caller's own session is gone, so the cookie must go with it.
        response.delete_cookie(COOKIE_NAME, **_session_cookie_attributes())

    return {"ok": True, "revoked": len(revoked)}


@router.get("/api/auth/me")
def read_me(session: SessionDep, user: CurrentUser) -> dict[str, object]:
    return user_payload(user, ensure_user_settings(session, user))


@router.post("/api/auth/password")
def change_password(
    payload: ChangePasswordRequest, session: SessionDep, user: CurrentUser
) -> dict[str, bool]:
    """Change the caller's own password, behind the shared re-auth guard.

    A wrong current password is still a 400 and a successful change still returns
    ``{"ok": true}``; what is new is that repeated wrong attempts now spend the
    account's re-auth budget and are refused with 429 once it runs out, and that a
    correct password clears it.
    """
    _require_password(session, user, payload.current_password, action=CHANGE_PASSWORD_ACTION)
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
    """Create an account, behind the administrator's own password.

    The order is the contract: the capability check (``AdminUser``) runs first, then
    the re-auth guard, and only then is anything read or written. Creating an
    account -- particularly an ``admin`` one -- is how a stolen session would turn
    itself into a permanent backdoor, and the guard is the one thing that path
    cannot survive: the attacker holds a cookie, not the password.

    The password is checked before the duplicate-username test on purpose. A
    refused attempt must not be able to learn anything, and "that name is taken"
    is information.
    """
    _require_password(session, admin, payload.current_password, action=CREATE_USER_ACTION)

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
    """Update an account, behind the administrator's own password.

    Same order as account creation: capability, then the re-auth guard, then the
    target lookup. Checking the password *before* resolving ``user_id`` is what makes
    a refused request unable to tell an existing account from a missing one -- both
    answer the same 400 -- and what guarantees a refused request has no effect:
    changing someone's password or disabling them also revokes every session they
    have, and undoing that is not possible.
    """
    _require_password(session, admin, payload.current_password, action=UPDATE_USER_ACTION)

    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    # ``current_password`` is excluded rather than merely ignored: the re-auth guard
    # has already consumed it, and it must not reach the field assignments below nor
    # the audit payload, whose ``fields`` is a list of *account* fields that changed.
    changes = payload.model_dump(exclude_unset=True, exclude={"current_password"})
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
