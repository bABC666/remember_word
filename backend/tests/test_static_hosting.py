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
