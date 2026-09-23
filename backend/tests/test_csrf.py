"""Same-origin enforcement on write requests (Phase 2.8, S-2).

The rule under test is deliberately narrow: a request using an unsafe method must
name the origin it was addressed to (``Origin``, falling back to ``Referer``). Two
kinds of test are needed for that, and they answer different questions:

* the decision table is unit-tested directly, because the interesting cases -- a
  bare ``Host`` behind a TLS proxy, an explicit default port, an IPv6 literal, a
  ``null`` origin -- are hard to produce through a real HTTP client;
* the wiring is tested through the application, because what matters there is not
  the boolean but its consequences: a refused request must leave no trace at all
  (no session resolved, no ``last_seen_at`` written, no audit row, no batch, no
  cookie), including on the endpoints that carry no session cookie to begin with.

Every test that must not look like a browser clears the header the rest of the suite
gets from ``conftest``.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import get_settings
from app.csrf import authority_of, request_authority, write_is_allowed
from app.main import app
from app.models import HistoryEvent, ImportBatch, ReviewEvent, User, UserSession, UserSettings

SAME_ORIGIN = "http://testserver"
CROSS_ORIGIN = "https://evil.example"
#: The password ``conftest`` gives every world's user.
PASSWORD = "test-password-123"


# --- clients -----------------------------------------------------------------


@pytest.fixture()
def originless():
    """A client that sends neither ``Origin`` nor ``Referer``, like curl or an old browser."""
    client = TestClient(app)
    client.__enter__()
    client.headers.clear()
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


def client_with(origin: str) -> TestClient:
    """A client that presents exactly this origin, overriding the suite default."""
    client = TestClient(app)
    client.__enter__()
    client.headers["Origin"] = origin
    return client


def close(client: TestClient) -> None:
    client.__exit__(None, None, None)


def png_bytes() -> bytes:
    """A real (tiny) PNG, because the upload path rejects bytes that are not an image."""
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def state_fingerprint(world) -> dict[str, object]:
    """The rows a write would have changed, so "nothing happened" is checkable."""
    with world.session() as session:
        return {
            "review_events": session.scalar(select(func.count()).select_from(ReviewEvent)),
            "history_events": session.scalar(select(func.count()).select_from(HistoryEvent)),
            "live_sessions": session.scalar(
                select(func.count()).select_from(UserSession).where(
                    UserSession.user_id == world.user_id,
                    UserSession.revoked_at.is_(None),
                )
            ),
            "daily_new_words": session.get(UserSettings, world.user_id).daily_new_words,
        }


# --- the decision table ------------------------------------------------------


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "get", "options"])
def test_safe_methods_are_never_checked(method: str) -> None:
    """A read from anywhere is still a read: the only over-GET side effects are self-scoped."""
    assert write_is_allowed(
        method=method,
        origin=CROSS_ORIGIN,
        referer=CROSS_ORIGIN,
        host_header="testserver",
        scheme="http",
    )


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post"])
def test_unsafe_methods_are_checked(method: str) -> None:
    assert write_is_allowed(
        method=method,
        origin=CROSS_ORIGIN,
        referer="",
        host_header="testserver",
        scheme="http",
    ) is False


def test_same_origin_is_allowed() -> None:
    assert write_is_allowed(
        method="POST",
        origin="http://testserver",
        referer="",
        host_header="testserver",
        scheme="http",
    )


def test_a_different_host_is_refused() -> None:
    assert not write_is_allowed(
        method="POST",
        origin="https://evil.example",
        referer="",
        host_header="127.0.0.1:8000",
        scheme="http",
    )


def test_the_port_is_part_of_the_comparison() -> None:
    """Same host, different port is a different origin -- otherwise :9999 could post here."""
    assert not write_is_allowed(
        method="POST",
        origin="http://127.0.0.1:9999",
        referer="",
        host_header="127.0.0.1:8000",
        scheme="http",
    )
    assert write_is_allowed(
        method="POST",
        origin="http://127.0.0.1:8000",
        referer="",
        host_header="127.0.0.1:8000",
        scheme="http",
    )


def test_the_scheme_itself_is_not_compared() -> None:
    """A TLS-terminating proxy makes the browser say https while FastAPI sees http."""
    assert write_is_allowed(
        method="POST",
        origin="https://vocab.example.com",
        referer="",
        host_header="vocab.example.com",
        scheme="https",
    )
    assert write_is_allowed(
        method="POST",
        origin="http://vocab.example.com:80",
        referer="",
        host_header="vocab.example.com:80",
        scheme="http",
    )


def test_a_bare_host_behind_http_does_not_match_an_https_origin() -> None:
    """Documents the one deployment coupling: without --proxy-headers the scheme is http.

    ``Origin: https://vocab.example.com`` implies port 443 while a bare ``Host`` with an
    http scheme implies 80, so the request is refused. That is the loud, fixable failure
    the proxy deployment is required to avoid (uvicorn --proxy-headers), not a silent
    hole: the check still refuses the cross-origin case it exists for.
    """
    assert not write_is_allowed(
        method="POST",
        origin="https://vocab.example.com",
        referer="",
        host_header="vocab.example.com",
        scheme="http",
    )


def test_referer_is_the_fallback_when_origin_is_absent() -> None:
    assert write_is_allowed(
        method="POST",
        origin="",
        referer="http://testserver/settings",
        host_header="testserver",
        scheme="http",
    )


def test_a_cross_origin_referer_is_refused() -> None:
    assert not write_is_allowed(
        method="POST",
        origin="",
        referer="https://evil.example/attack.html",
        host_header="testserver",
        scheme="http",
    )


def test_no_origin_and_no_referer_is_refused_by_default() -> None:
    assert not write_is_allowed(
        method="POST", origin="", referer="", host_header="testserver", scheme="http"
    )
    assert write_is_allowed(
        method="POST",
        origin="",
        referer="",
        host_header="testserver",
        scheme="http",
        allow_missing_origin=True,
    )


def test_a_null_origin_is_never_trusted() -> None:
    """A sandboxed iframe or a file:// page says "null"; that is not "not a browser"."""
    for allow in (False, True):
        assert not write_is_allowed(
            method="POST",
            origin="null",
            referer="",
            host_header="testserver",
            scheme="http",
            allow_missing_origin=allow,
        )


def test_trusted_origins_are_accepted_by_authority() -> None:
    trusted = ("https://other.example",)
    assert write_is_allowed(
        method="POST",
        origin="https://other.example",
        referer="",
        host_header="vocab.example.com",
        scheme="https",
        trusted_origins=trusted,
    )
    # A trusted host on another port is still another origin.
    assert not write_is_allowed(
        method="POST",
        origin="https://other.example:8443",
        referer="",
        host_header="vocab.example.com",
        scheme="https",
        trusted_origins=trusted,
    )
    # Blank entries in the setting must not turn into a wildcard.
    assert not write_is_allowed(
        method="POST",
        origin=CROSS_ORIGIN,
        referer="",
        host_header="vocab.example.com",
        scheme="https",
        trusted_origins=("", "   "),
    )


@pytest.mark.parametrize("value", ["http://", "not a url", "://", "https:///path", ""])
def test_unusable_origins_are_refused(value: str) -> None:
    assert authority_of(value) is None


@pytest.mark.parametrize("host", ["evil.example/path", "user@host", "bad host", ""])
def test_unusable_host_headers_are_refused(host: str) -> None:
    assert request_authority(host, "http") is None
    assert not write_is_allowed(
        method="POST",
        origin="http://testserver",
        referer="",
        host_header=host,
        scheme="http",
    )


def test_hosts_are_compared_case_insensitively_and_ipv6_works() -> None:
    assert write_is_allowed(
        method="POST",
        origin="http://TESTserver",
        referer="",
        host_header="testserver",
        scheme="http",
    )
    assert write_is_allowed(
        method="POST",
        origin="http://[::1]:8000",
        referer="",
        host_header="[::1]:8000",
        scheme="http",
    )


# --- the wiring, through the application -------------------------------------


def test_a_same_origin_write_is_allowed(world) -> None:
    response = world.client.put("/api/settings", json={"daily_new_words": 42})
    assert response.status_code == 200, response.text
    assert state_fingerprint(world)["daily_new_words"] == 42


def test_a_cross_origin_write_is_refused_and_changes_nothing(world) -> None:
    state_id, _entry_id = world.add_word("csrf-target")
    before = state_fingerprint(world)

    response = world.client.post(
        f"/api/study/word-states/{state_id}/review",
        json={"result": "know", "source": "daily", "review_type": "recall"},
        headers={"Origin": CROSS_ORIGIN},
    )

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "请求来源不可信，已拒绝"}
    # No review event, no audit row, and no session activity: the middleware refuses
    # before any dependency runs.
    assert state_fingerprint(world) == before
    # The refused value is caller-controlled, so it must never be echoed back.
    assert CROSS_ORIGIN not in response.text
    assert response.headers.get("cache-control") == "no-store"


def test_a_cross_origin_referer_write_is_refused(world) -> None:
    response = world.client.put(
        "/api/settings",
        json={"daily_new_words": 42},
        headers={"Origin": "", "Referer": f"{CROSS_ORIGIN}/attack.html"},
    )
    assert response.status_code == 403
    assert state_fingerprint(world)["daily_new_words"] != 42


def test_a_write_with_no_origin_at_all_is_refused(originless) -> None:
    """403, not 401: the refusal happens before authentication is even attempted."""
    response = originless.put("/api/settings", json={"daily_new_words": 42})
    assert response.status_code == 403
    assert response.json()["detail"] == "请求来源不可信，已拒绝"


def test_a_null_origin_is_refused(world) -> None:
    response = world.client.post(
        "/api/auth/logout", headers={"Origin": "null"}
    )
    assert response.status_code == 403


def test_reads_are_untouched(originless) -> None:
    """No Origin is needed for reads, including the SPA shell and the health probe."""
    assert originless.get("/api/health").status_code == 200
    assert originless.get("/openapi.json").status_code == 200
    assert originless.get("/").status_code == 200
    # Reaches the route and answers 401 rather than being refused by the middleware.
    assert originless.get("/api/auth/me").status_code == 401


def test_options_is_untouched(originless) -> None:
    """OPTIONS is a safe method: it must not be turned into a 403."""
    assert originless.options("/api/settings").status_code != 403


def test_login_is_not_exempt(world) -> None:
    """A forged login signs the victim into the attacker's account."""
    before = state_fingerprint(world)
    forger = client_with(CROSS_ORIGIN)
    try:
        response = forger.post(
            "/api/auth/login",
            json={"username": world.username, "password": PASSWORD},
        )
        assert response.status_code == 403
        assert "set-cookie" not in response.headers
        assert state_fingerprint(world)["history_events"] == before["history_events"]
    finally:
        close(forger)

    # The same request from this origin works, which is what makes the 403 above the
    # origin check and not something else.
    honest = client_with(SAME_ORIGIN)
    try:
        ok = honest.post("/api/auth/login", json={"username": world.username, "password": PASSWORD})
        assert ok.status_code == 200, ok.text
        assert "set-cookie" in ok.headers
    finally:
        close(honest)


def test_logout_is_not_exempt(world) -> None:
    response = world.client.post("/api/auth/logout", headers={"Origin": CROSS_ORIGIN})
    assert response.status_code == 403
    # The session is still usable: a cross-site page must not be able to sign anyone out.
    assert world.client.get("/api/auth/me").status_code == 200


def test_import_uploads_are_protected(world) -> None:
    """The two multipart endpoints are the ones a cross-site form could reach."""
    payload = png_bytes()
    before = state_fingerprint(world)

    forged = world.client.post(
        "/api/imports",
        files={"files": ("page.png", payload, "image/png")},
        headers={"Origin": CROSS_ORIGIN},
    )
    assert forged.status_code == 403
    with world.session() as session:
        # Scoped to this user: the suite shares one database, so a whole-table count
        # would include batches other tests created.
        assert (
            session.scalar(
                select(func.count())
                .select_from(ImportBatch)
                .where(ImportBatch.user_id == world.user_id)
            )
            == 0
        )

    # The same upload from this origin is accepted, so the endpoint itself is fine.
    accepted = world.client.post(
        "/api/imports", files={"files": ("page.png", payload, "image/png")}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["images"]
    assert state_fingerprint(world)["history_events"] == before["history_events"]


def test_appending_import_images_is_protected(world) -> None:
    payload = png_bytes()
    created = world.client.post(
        "/api/imports", files={"files": ("page.png", payload, "image/png")}
    )
    assert created.status_code == 200, created.text
    batch_id = created.json()["id"]
    assert len(created.json()["images"]) == 1

    forged = world.client.post(
        f"/api/imports/{batch_id}/images",
        files={"files": ("second.png", payload, "image/png")},
        headers={"Origin": CROSS_ORIGIN},
    )
    assert forged.status_code == 403
    unchanged = world.client.get(f"/api/imports/{batch_id}")
    assert len(unchanged.json()["images"]) == 1


def test_admin_endpoints_are_protected(make_world) -> None:
    """The highest-impact write there is: creating an account needs only a session."""
    admin = make_world("csrf-admin", role="admin")
    with admin.session() as session:
        before = session.scalar(select(func.count()).select_from(User))

    response = admin.client.post(
        "/api/users",
        json={"username": "backdoor", "password": "backdoor-pass", "role": "admin"},
        headers={"Origin": CROSS_ORIGIN},
    )

    assert response.status_code == 403
    with admin.session() as session:
        assert session.scalar(select(func.count()).select_from(User)) == before
        assert session.scalar(select(User).where(User.username == "backdoor")) is None


def test_trusted_origins_setting_is_honoured(world, monkeypatch) -> None:
    monkeypatch.setenv("VOCAB_CSRF_TRUSTED_ORIGINS", "https://trusted.example, https://other.example")
    get_settings.cache_clear()

    allowed = world.client.put(
        "/api/settings",
        json={"daily_new_words": 42},
        headers={"Origin": "https://trusted.example"},
    )
    assert allowed.status_code == 200, allowed.text

    still_refused = world.client.put(
        "/api/settings",
        json={"daily_new_words": 43},
        headers={"Origin": CROSS_ORIGIN},
    )
    assert still_refused.status_code == 403
    assert state_fingerprint(world)["daily_new_words"] == 42


def test_allow_missing_origin_setting_is_honoured(originless, monkeypatch) -> None:
    """The escape hatch for scripted clients, off unless an operator asks for it."""
    refused = originless.put("/api/settings", json={"daily_new_words": 42})
    assert refused.status_code == 403

    monkeypatch.setenv("VOCAB_CSRF_ALLOW_MISSING_ORIGIN", "true")
    get_settings.cache_clear()

    # Past the middleware now, and answered on its own merits: this client has no
    # session, so the endpoint (not the middleware) refuses it.
    allowed_through = originless.put("/api/settings", json={"daily_new_words": 42})
    assert allowed_through.status_code == 401


def test_the_rejection_is_not_a_gateway_to_the_session(originless, world) -> None:
    """A refused write must not have refreshed the caller's own session row."""
    with world.session() as session:
        before = session.scalars(
            select(UserSession).where(UserSession.user_id == world.user_id)
        ).all()
        seen_before = [row.last_seen_at for row in before]

    assert originless.put("/api/settings", json={"daily_new_words": 42}).status_code == 403

    with world.session() as session:
        after = session.scalars(
            select(UserSession).where(UserSession.user_id == world.user_id)
        ).all()
        assert [row.last_seen_at for row in after] == seen_before
        assert [row.revoked_at for row in after] == [row.revoked_at for row in before]
