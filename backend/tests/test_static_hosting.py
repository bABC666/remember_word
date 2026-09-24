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


def test_pwa_manifest_worker_and_shared_icon_are_public_static_assets() -> None:
    """The offline shell is installable without making any API data cacheable."""
    from app.main import app

    with TestClient(app) as client:
        manifest = client.get("/manifest.webmanifest")
        worker = client.get("/sw.js")
        icon = client.get("/icons/shici-192.png")

    assert manifest.status_code == 200
    assert manifest.json()["display"] == "standalone"
    assert manifest.json()["icons"][0]["src"] == "/icons/shici-192.png"
    assert worker.status_code == 200
    assert "url.pathname.startsWith('/api/')" in worker.text
    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/png")
