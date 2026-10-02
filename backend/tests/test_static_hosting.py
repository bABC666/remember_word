from pathlib import Path

from fastapi.testclient import TestClient


def test_built_frontend_is_served_for_spa_routes() -> None:
    from app.main import app

    with TestClient(app) as client:
        root = client.get("/")
        nested = client.get("/study")
    assert root.status_code == 200
    assert nested.status_code == 200
    assert "<title>拾词</title>" in root.text
    assert "<title>拾词</title>" in nested.text


def test_pwa_shell_assets_are_public_and_never_cache_api_data(monkeypatch) -> None:
    """The installable shell is served statically without widening the cache scope.

    Point the fallback at the versioned public assets, which Vite copies to dist.
    This exercises the route and worker without requiring a prior frontend build.
    The HTTP-level ``/api`` boundary is covered by ``test_spa_fallback.py``.
    """
    from app import main

    public = Path(__file__).resolve().parents[2] / "frontend" / "public"
    monkeypatch.setattr(main, "frontend_dist", public)

    with TestClient(main.app) as client:
        manifest = client.get("/manifest.webmanifest")
        worker = client.get("/sw.js")
        icon = client.get("/icons/shici-192.png")
        touch_icon = client.get("/icons/shici-180.png")

    assert manifest.status_code == 200, manifest.text
    assert manifest.json()["display"] == "standalone"
    assert manifest.json()["icons"][0]["src"] == "/icons/shici-192.png"

    assert worker.status_code == 200, worker.text
    assert worker.headers["content-type"].startswith(("text/javascript", "application/javascript"))
    # Every spelling of the API namespace is refused before any cache is consulted.
    assert "url.pathname === '/api'" in worker.text
    assert "url.pathname.startsWith('/api/')" in worker.text
    assert "request.method !== 'GET'" in worker.text
    assert "url.origin !== self.location.origin" in worker.text

    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/png")
    assert touch_icon.status_code == 200
    assert touch_icon.headers["content-type"].startswith("image/png")
