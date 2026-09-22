"""Session lifetime: idle timeout, activity tracking, cleanup and one cookie name.

Three properties are pinned here, all of them over columns that already existed
(``created_at``, ``last_seen_at``, ``expires_at``, ``revoked_at``) -- no schema
change, no migration:

* a session is *live* only while it is unrevoked, inside its absolute
  ``expires_at``, and used within ``VOCAB_SESSION_IDLE_DAYS``;
* an authenticated request records that use without rotating the token or
  extending the absolute deadline, and a rejected request records nothing;
* cleanup deletes exactly the sessions that can never be accepted again, so a
  valid-but-unused session survives.

The cookie name is asserted to have a single definition in the codebase and to be
the value every location derives from, because the original defect was a second,
hard-coded copy of the name in ``api/deps.py``.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.conftest import TEST_PASSWORD

APP_DIR = Path(__file__).resolve().parents[1] / "app"
#: The wire name. Changing it logs everybody out, so it is asserted literally.
COOKIE_NAME_LITERAL = "shici_session"


def now() -> datetime:
    return datetime.now(UTC)


# --- helpers --------------------------------------------------------------


def issue_session(
    world,
    *,
    created_at: datetime | None = None,
    last_seen_at: datetime | None = None,
    revoked_at: datetime | None = None,
) -> tuple[str, int]:
    """Create a session row with explicit timestamps. Returns (token, id)."""
    from app.services.auth import create_session

    with world.session() as session:
        token, record = create_session(session, world.reload_user(), now=created_at)
        if last_seen_at is not None:
            record.last_seen_at = last_seen_at
        if revoked_at is not None:
            record.revoked_at = revoked_at
        if last_seen_at is not None or revoked_at is not None:
            session.add(record)
            session.commit()
        return token, record.id


def session_rows(world) -> list:
    from app.models import UserSession

    with world.session() as session:
        return list(session.query(UserSession).order_by(UserSession.id).all())


def session_state(world, session_id: int) -> dict[str, object]:
    from app.models import UserSession

    with world.session() as session:
        record = session.get(UserSession, session_id)
        assert record is not None, f"session {session_id} is gone"
        return {
            "token_hash": record.token_hash,
            "created_at": record.created_at,
            "expires_at": record.expires_at,
            "last_seen_at": record.last_seen_at,
            "revoked_at": record.revoked_at,
        }


def set_last_seen(world, session_id: int, value: datetime) -> None:
    from app.models import UserSession

    with world.session() as session:
        record = session.get(UserSession, session_id)
        assert record is not None
        record.last_seen_at = value
        session.add(record)
        session.commit()


def authenticate_as(world, token: str):
    """Send exactly this session token and nothing else."""
    world.client.cookies.clear()
    world.client.cookies.set(COOKIE_NAME_LITERAL, token)
    return world.client.get("/api/auth/me")


# --- 1. a login produces a live session -----------------------------------


def test_login_issues_exactly_one_live_session(world) -> None:
    from app.services.auth import session_is_live

    rows = session_rows(world)
    assert len(rows) == 1, "the login in the fixture must create exactly one session"
    record = rows[0]
    assert record.user_id == world.user_id
    assert record.revoked_at is None
    assert session_is_live(record)
    # The absolute deadline is issued at login and is the only thing that sets it.
    issued = record.created_at.replace(tzinfo=UTC) if record.created_at.tzinfo is None else record.created_at
    assert record.expires_at.replace(tzinfo=UTC) == issued + timedelta(days=30)
    # Nothing has used it yet, so the idle clock still runs from creation.
    assert record.last_seen_at is None

    assert world.client.get("/api/auth/me").status_code == 200


# --- 2. activity is recorded, and only activity --------------------------


def test_an_authenticated_request_records_last_seen_at(world) -> None:
    session_id = session_rows(world)[0].id
    assert session_state(world, session_id)["last_seen_at"] is None

    before = session_state(world, session_id)
    assert world.client.get("/api/auth/me").status_code == 200

    after = session_state(world, session_id)
    assert after["last_seen_at"] is not None, "a successful request must record the use"
    assert after["last_seen_at"].replace(tzinfo=UTC) > now() - timedelta(minutes=1)
    # Touching records the use and nothing else: same row, same token, same deadline.
    assert after["token_hash"] == before["token_hash"]
    assert after["expires_at"] == before["expires_at"]
    assert after["created_at"] == before["created_at"]
    assert after["revoked_at"] is None
    assert len(session_rows(world)) == 1, "touching must not create a session"


def test_touch_is_throttled_so_reads_do_not_become_writes(world) -> None:
    session_id = session_rows(world)[0].id

    recent = now() - timedelta(minutes=1)
    set_last_seen(world, session_id, recent)
    assert world.client.get("/api/auth/me").status_code == 200
    assert session_state(world, session_id)["last_seen_at"].replace(tzinfo=UTC) == recent, (
        "a session touched moments ago must not be rewritten on every request"
    )

    stale = now() - timedelta(minutes=30)
    set_last_seen(world, session_id, stale)
    assert world.client.get("/api/auth/me").status_code == 200
    refreshed = session_state(world, session_id)["last_seen_at"]
    assert refreshed.replace(tzinfo=UTC) > stale, "a stale timestamp must be refreshed"


def test_a_rejected_request_records_nothing(world) -> None:
    token, session_id = issue_session(world)
    before = session_state(world, session_id)

    world.client.cookies.clear()
    world.client.cookies.set(COOKIE_NAME_LITERAL, "not-a-real-token")
    assert world.client.get("/api/auth/me").status_code == 401
    assert world.client.get("/api/words").status_code == 401

    assert session_state(world, session_id) == before
    # A token that was never valid did not create anything either.
    assert len(session_rows(world)) == 2  # the fixture's login plus this one
    assert token  # the real token is still usable
    assert authenticate_as(world, token).status_code == 200


# --- 3./4. dead sessions answer 401 ---------------------------------------


def test_an_expired_session_is_refused_with_401(world) -> None:
    # Issued 40 days ago: the absolute 30-day deadline is in the past, and a fresh
    # last_seen_at must not bring it back.
    token, session_id = issue_session(
        world, created_at=now() - timedelta(days=40), last_seen_at=now() - timedelta(hours=1)
    )
    response = authenticate_as(world, token)
    assert response.status_code == 401, response.text

    # The refusal is a read: the row survives, unrevoked, for the cleanup to remove.
    assert session_state(world, session_id)["revoked_at"] is None


def test_a_revoked_session_is_refused_with_401(world) -> None:
    token, _ = issue_session(world, revoked_at=now())
    response = authenticate_as(world, token)
    assert response.status_code == 401, response.text


def test_an_idle_session_is_refused_with_401(world) -> None:
    # Never used since it was issued 10 days ago, and well inside its absolute
    # lifetime: only the idle rule can reject this one.
    token, session_id = issue_session(world, created_at=now() - timedelta(days=10))
    state = session_state(world, session_id)
    assert state["expires_at"].replace(tzinfo=UTC) > now(), "must not be absolutely expired"

    response = authenticate_as(world, token)
    assert response.status_code == 401, response.text

    # A session idle by its last use is refused the same way.
    used, used_id = issue_session(
        world,
        created_at=now() - timedelta(days=10),
        last_seen_at=now() - timedelta(days=9),
    )
    assert session_state(world, used_id)["last_seen_at"] is not None
    assert authenticate_as(world, used).status_code == 401


def test_a_session_used_within_the_idle_window_is_accepted(world) -> None:
    token, session_id = issue_session(
        world,
        created_at=now() - timedelta(days=20),
        last_seen_at=now() - timedelta(days=6),
    )
    assert session_state(world, session_id)["expires_at"].replace(tzinfo=UTC) > now()
    assert authenticate_as(world, token).status_code == 200


def test_the_idle_check_can_be_switched_off(world, monkeypatch) -> None:
    from app.config import get_settings

    token, session_id = issue_session(
        world, created_at=now() - timedelta(days=10), last_seen_at=now() - timedelta(days=10)
    )
    assert authenticate_as(world, token).status_code == 401

    monkeypatch.setenv("VOCAB_SESSION_IDLE_DAYS", "0")
    get_settings.cache_clear()
    # 0 disables the idle rule; the absolute lifetime still decides.
    assert session_state(world, session_id)["expires_at"].replace(tzinfo=UTC) > now()
    assert authenticate_as(world, token).status_code == 200


# --- 5. cleanup deletes the dead and only the dead ------------------------


def test_prune_deletes_only_sessions_that_can_never_be_used_again(world) -> None:
    from app.models import UserSession
    from app.services.auth import prune_sessions

    live_used_token, live_used = issue_session(
        world, created_at=now() - timedelta(days=1), last_seen_at=now() - timedelta(hours=1)
    )
    # Logged in and never used: inside the idle window, so it must survive.
    live_unused_token, live_unused = issue_session(world, created_at=now() - timedelta(hours=1))
    _revoked_token, revoked = issue_session(world, created_at=now() - timedelta(days=1), revoked_at=now())
    # Absolutely expired even though it was used an hour ago.
    _expired_token, expired = issue_session(
        world, created_at=now() - timedelta(days=40), last_seen_at=now() - timedelta(hours=1)
    )
    _idle_token, idle_unused = issue_session(world, created_at=now() - timedelta(days=10))
    _idle_used_token, idle_used = issue_session(
        world, created_at=now() - timedelta(days=10), last_seen_at=now() - timedelta(days=9)
    )
    fixture_session = session_rows(world)[0].id  # the fixture's login, just used

    before = {row.id for row in session_rows(world)}
    with world.session() as session:
        removed = prune_sessions(session)

    survivors = {row.id for row in session_rows(world)}
    removed_ids = before - survivors
    # Named explicitly, so a survivor is identified rather than counted.
    assert removed_ids == {revoked, expired, idle_unused, idle_used}, (
        f"removed={sorted(removed_ids)} survivors={sorted(survivors)}"
    )
    assert removed == 4, f"reported {removed} removals"
    assert survivors == {live_used, live_unused, fixture_session}

    # Both survivors still authenticate, including the unused one.
    assert authenticate_as(world, live_used_token).status_code == 200
    assert authenticate_as(world, live_unused_token).status_code == 200
    with world.session() as session:
        assert session.get(UserSession, live_unused) is not None


def test_prune_and_the_live_check_agree_on_the_boundary(world) -> None:
    """The cleanup must not delete a session the API would still have accepted.

    Both rules are evaluated at exactly the idle cutoff, where an off-by-one would
    show up as a session that answers 200 and is deleted anyway.
    """
    from app.config import get_settings
    from app.models import UserSession
    from app.services.auth import prune_sessions, session_is_live

    idle = timedelta(days=get_settings().session_idle_days)
    moment = now()
    _token, session_id = issue_session(world, created_at=moment - idle, last_seen_at=moment - idle)

    with world.session() as session:
        record = session.get(UserSession, session_id)
        assert record is not None
        assert session_is_live(record, now=moment) is False, "the boundary counts as dead"
        removed = prune_sessions(session, now=moment)

    assert removed == 1
    assert session_id not in {row.id for row in session_rows(world)}


def test_the_startup_cleanup_leaves_valid_sessions_working(world) -> None:
    """The lifespan prune must not log anybody out."""
    from fastapi.testclient import TestClient

    from app.main import app

    token, session_id = issue_session(world, created_at=now() - timedelta(hours=2))
    _dead_token, dead_id = issue_session(world, revoked_at=now())

    with TestClient(app) as restarted:
        # A fresh startup, exactly like a server restart.
        assert restarted.get("/api/health").status_code == 200

    survivors = {row.id for row in session_rows(world)}
    assert session_id in survivors, "a valid session must survive the startup prune"
    assert dead_id not in survivors, "a revoked session must not survive it"
    assert authenticate_as(world, token).status_code == 200


def test_prune_sessions_command_reports_what_it_removed(world, capsys) -> None:
    from app import cli

    token, live_id = issue_session(world, created_at=now() - timedelta(hours=1))
    _dead_token, dead_id = issue_session(world, revoked_at=now())

    assert cli.main(["prune-sessions"]) == 0

    output = capsys.readouterr().out
    assert "已清理 1 条" in output, output
    survivors = {row.id for row in session_rows(world)}
    assert live_id in survivors and dead_id not in survivors
    assert authenticate_as(world, token).status_code == 200


# --- 6. one cookie name, everywhere ---------------------------------------


def test_the_cookie_name_has_exactly_one_definition() -> None:
    """The defect this guards: a second, hard-coded copy of the name."""
    literal_uses: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == COOKIE_NAME_LITERAL:
                location = path.relative_to(APP_DIR.parent).as_posix()
                literal_uses.append(f"{location}:{node.lineno}")

    assert len(literal_uses) == 1, (
        f"the cookie name must exist as one constant, found: {literal_uses}"
    )
    assert literal_uses[0].startswith("app/services/auth.py"), literal_uses


def test_the_wire_name_is_unchanged() -> None:
    from app.services.auth import COOKIE_NAME

    assert COOKIE_NAME == COOKIE_NAME_LITERAL, (
        "renaming the cookie logs every existing client out; do it deliberately"
    )


def test_every_cookie_location_derives_from_the_constant() -> None:
    """No location may hold its own copy, so a rename moves all of them.

    ``api/deps.py`` resolves the cookie parameter when FastAPI builds the routes,
    ``api/auth.py`` reads the constant each time it runs. Both must take the name
    from ``app.services.auth`` -- a literal in either place would break the login,
    the read or the logout independently of the other two.
    """
    deps = ast.parse((APP_DIR / "api" / "deps.py").read_text(encoding="utf-8"))
    aliases = [
        keyword.value
        for node in ast.walk(deps)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "alias" and isinstance(node.func, ast.Name) and node.func.id == "Cookie"
    ]
    assert len(aliases) == 1, f"expected exactly one Cookie(alias=...) declaration: {aliases}"
    assert isinstance(aliases[0], ast.Name) and aliases[0].id == "COOKIE_NAME", ast.dump(aliases[0])

    for module in ("api/deps.py", "api/auth.py"):
        tree = ast.parse((APP_DIR / module).read_text(encoding="utf-8"))
        imported = {
            name.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "app.services.auth"
            for name in node.names
        }
        assert "COOKIE_NAME" in imported, f"{module} must import the constant, not restate it"


def cookie_aliases(schema: dict) -> tuple[set[str], set[str]]:
    """(declared cookie names, operations that declare none) from the OpenAPI schema.

    The schema is the public contract: FastAPI flattens the dependency's cookie
    parameter into every operation that requires it, so a second hard-coded name in
    ``api/deps.py`` would show up here as a different name -- which is exactly the
    defect this test exists to catch.
    """
    declared: set[str] = set()
    without: set[str] = set()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            names = {
                parameter["name"]
                for parameter in operation.get("parameters", [])
                if parameter.get("in") == "cookie"
            }
            declared |= names
            if not names:
                without.add(f"{method.upper()} {path}")
    return declared, without


def test_every_runtime_cookie_site_agrees_with_the_constant(world) -> None:
    from app.main import app
    from app.services.auth import COOKIE_NAME

    declared, without = cookie_aliases(app.openapi())
    assert declared == {COOKIE_NAME}, f"routes declare {declared}, constant is {COOKIE_NAME!r}"
    # Only the three endpoints that must work without a session may omit it, so a
    # new authenticated endpoint that forgot the dependency fails here too. Logout
    # is one of them on purpose: it reads the cookie itself so that signing out
    # always clears it, even when the session behind it is already dead.
    assert without == {
        "POST /api/auth/login",
        "POST /api/auth/logout",
        "GET /api/health",
    }, sorted(without)

    login = world.client.post(
        "/api/auth/login", json={"username": world.username, "password": TEST_PASSWORD}
    )
    assert login.status_code == 200, login.text
    header = login.headers["set-cookie"]
    assert header.startswith(f"{COOKIE_NAME}="), header
    lowered = header.lower()
    assert "httponly" in lowered, header
    assert "path=/" in lowered, header
    assert "samesite=lax" in lowered, header

    assert world.client.get("/api/auth/me").status_code == 200
    logout = world.client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert f"{COOKIE_NAME}=" in logout.headers["set-cookie"], logout.headers["set-cookie"]
    assert world.client.get("/api/auth/me").status_code == 401


def test_renaming_the_constant_moves_the_call_time_sites(monkeypatch, world) -> None:
    """Proves the login and logout sites read the constant rather than a literal.

    The dependency's alias is resolved once, when FastAPI builds the routes, so it
    is pinned by the AST check above instead of by this runtime rename.
    """
    monkeypatch.setattr("app.api.auth.COOKIE_NAME", "renamed_session")

    login = world.client.post(
        "/api/auth/login", json={"username": world.username, "password": TEST_PASSWORD}
    )
    assert login.status_code == 200, login.text
    assert login.headers["set-cookie"].startswith("renamed_session=")

    # The fixture's original cookie still authenticates this request, so the
    # logout below reaches `delete_cookie`.
    logout = world.client.post("/api/auth/logout")
    assert logout.status_code == 200, logout.text
    assert "renamed_session=" in logout.headers["set-cookie"], logout.headers["set-cookie"]
