"""The SPA fallback route and its ``/api`` exception (Phase 2.8 T11 / G7 "T-3").

``frontend`` is a catch-all ``GET`` route, and that is what lets a deep link such as
``/study`` load the single-page application. Without an exception it also swallowed
every unknown ``/api/**`` path: a caller that mistyped an endpoint was answered ``200``
with ``index.html``, so a failed API call looked like a success and the body was an
HTML document rather than JSON.

An exception in a catch-all route is exactly the kind of change that quietly breaks
the cases the route exists for, so the tests below pin both halves: the new ``404`` for
unknown API paths, and everything that must keep working -- ordinary frontend routes,
the real API surface, the statically mounted ``/assets`` tree, and the same-origin write
check, which runs as middleware and therefore *before* routing. A cross-origin write to
an unknown API path must still be refused rather than answered, which is the one way
this change could have become a hole.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

#: A string that only the built SPA shell contains, so "is this the frontend?" is a
#: question about the response body rather than about its content type.
SPA_MARKER = "<title>拾词</title>"
SAME_ORIGIN = "http://testserver"
CROSS_ORIGIN = "https://evil.example"
#: The one message every unknown API path answers with. It is a constant, not an echo
#: of the request: a 404 body must never reflect caller-controlled input back.
NOT_FOUND_DETAIL = "接口不存在"

#: Paths under the API namespace that no route serves. ``/api`` and ``/api/`` are the
#: namespace itself, which is just as unknown as a mistyped endpoint below it.
UNKNOWN_API_PATHS = ["/api", "/api/", "/api/nope", "/api/study/nope", "/api/nope/deep/path"]


def is_json(response) -> bool:
    return response.headers.get("content-type", "").startswith("application/json")


# --- the new exception -------------------------------------------------------


@pytest.mark.parametrize("path", UNKNOWN_API_PATHS)
def test_unknown_api_paths_answer_404_json(path: str) -> None:
    with TestClient(app) as client:
        response = client.get(path)
    assert response.status_code == 404, response.text
    assert is_json(response), response.headers.get("content-type")
    assert response.json() == {"detail": NOT_FOUND_DETAIL}
    assert SPA_MARKER not in response.text


def test_a_non_get_method_on_an_unknown_api_path_is_never_the_shell() -> None:
    """Only ``GET`` is the fallback, so the other methods are answered by the router.

    FastAPI does not add ``HEAD`` to a ``GET`` route (unlike Starlette's plain
    ``Route``), so ``HEAD``, ``POST`` and ``OPTIONS`` on an unknown API path are refused
    with the router's 405. That is the honest boundary of this fix: the defect being
    repaired is the ``200`` HTML answer, and none of these are that.
    """
    with TestClient(app) as client:
        for method in ("head", "post", "options"):
            response = getattr(client, method)("/api/nope")
            assert response.status_code == 405, (method, response.text)
            assert is_json(response)
            assert SPA_MARKER not in response.text


# --- what the fallback still has to do ---------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/study",
        "/settings",
        "/articles/42",
        # A path that merely *starts* with the same letters is a frontend route: only a
        # whole ``api`` segment is the API namespace.
        "/apiary",
        "/api-docs",
    ],
)
def test_frontend_routes_still_serve_the_spa_shell(path: str) -> None:
    with TestClient(app) as client:
        response = client.get(path)
    assert response.status_code == 200, response.text
    assert SPA_MARKER in response.text


def test_real_api_endpoints_are_untouched() -> None:
    with TestClient(app) as client:
        health = client.get("/api/health")
        anonymous = client.get("/api/auth/me")
        schema = client.get("/openapi.json")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "app": "拾词"}
    # 401 comes from the endpoint's own dependency, so the request reached the API
    # rather than being intercepted by the fallback.
    assert anonymous.status_code == 401
    assert is_json(anonymous)

    assert schema.status_code == 200
    paths = schema.json()["paths"]
    assert "/api/health" in paths
    # The fallback itself stays out of the documented API.
    assert not [route for route in paths if route.startswith("/{")]


def test_static_assets_are_served_but_a_missing_one_is_not_the_shell() -> None:
    """``/assets`` is a mount in front of the fallback: it must keep answering as one."""
    from app.config import get_settings

    bundles = sorted((get_settings().frontend_dist / "assets").glob("*.js"))
    assert bundles, "the frontend must be built for this test to mean anything"

    with TestClient(app) as client:
        served = client.get(f"/assets/{bundles[0].name}")
        missing = client.get("/assets/does-not-exist-9f3a.js")

    assert served.status_code == 200
    assert SPA_MARKER not in served.text

    # A missing static file is the mount's 404, never the SPA shell.
    assert missing.status_code == 404
    assert SPA_MARKER not in missing.text


# --- the ordering that must not regress --------------------------------------


def test_a_cross_origin_write_to_an_unknown_api_path_is_still_refused() -> None:
    """The write check is middleware, so it decides before routing -- 403, not 404.

    This is the one way the new exception could have become a hole: if an unknown API
    path were answered by routing before the origin was inspected, a refused write
    would have been replaced by an answer that never checked the origin at all.
    """
    with TestClient(app) as client:
        forged = client.post("/api/nope", headers={"Origin": CROSS_ORIGIN})
        same_origin = client.post("/api/nope", headers={"Origin": SAME_ORIGIN})

    assert forged.status_code == 403, forged.text
    assert forged.json() == {"detail": "请求来源不可信，已拒绝"}
    assert forged.headers.get("cache-control") == "no-store"

    # The fallback is a GET route, so a same-origin write to an unknown API path is
    # answered by the router's method check instead. Either answer is JSON: what must
    # never come back is the SPA shell.
    assert same_origin.status_code == 405, same_origin.text
    assert is_json(same_origin)
    assert SPA_MARKER not in same_origin.text
    assert SPA_MARKER not in forged.text


def _dispatch_name(entry) -> str | None:
    dispatch = entry.kwargs.get("dispatch")
    return getattr(dispatch, "__name__", None)


def test_the_write_check_is_the_outermost_middleware() -> None:
    """The order ``main`` documents: the origin check wraps the ``no-store`` one.

    Starlette runs ``app.user_middleware`` outside-in, so an earlier entry sees the
    request first. The order matters because a refused write must be answered before a
    session is resolved: the check is the outermost layer precisely so nothing else in
    the application runs for a request that is already known to be untrusted.
    """
    order = [_dispatch_name(entry) for entry in app.user_middleware]
    assert "_same_origin_write_check" in order, order
    assert "_no_store_auth_responses" in order, order
    assert order.index("_same_origin_write_check") < order.index("_no_store_auth_responses")
