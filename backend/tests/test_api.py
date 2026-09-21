from fastapi.testclient import TestClient


def test_health_endpoint_identifies_local_app() -> None:
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["app"] == "拾词"


def test_settings_never_return_plaintext_api_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-value-1234")
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["deepseek_api_key_configured"] is True
    assert payload["deepseek_api_key_masked"].endswith("1234")
    assert "secret-value-1234" not in response.text
