from fastapi.testclient import TestClient


def test_health_endpoint_identifies_local_app() -> None:
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["app"] == "拾词"


def test_settings_never_return_plaintext_api_key(monkeypatch, world) -> None:
    """The key is instance-level: reported as configured, never returned."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-value-1234")
    from app.config import get_settings

    get_settings.cache_clear()

    response = world.client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["deepseek_api_key_configured"] is True
    assert payload["deepseek_api_key_masked"].endswith("1234")
    assert "secret-value-1234" not in response.text


def test_settings_require_authentication() -> None:
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/api/settings").status_code == 401
        assert client.get("/api/settings/onboarding").status_code == 401


def test_onboarding_seen_state_is_persisted_per_user(world) -> None:
    """Onboarding is a personal preference, stored on the user's own row."""
    from app.models import UserSettings

    assert world.client.get("/api/settings/onboarding").json() == {"seen": False}
    assert world.client.post("/api/settings/onboarding").json() == {"seen": True}
    assert world.client.get("/api/settings/onboarding").json() == {"seen": True}

    with world.session() as session:
        record = session.get(UserSettings, world.user_id)
        assert record.onboarding_seen is True


def test_personal_settings_update_only_touches_the_callers_row(world) -> None:
    response = world.client.put(
        "/api/settings", json={"daily_new_words": 7, "article_length": 420}
    )
    assert response.status_code == 200
    assert response.json()["daily_new_words"] == 7
    assert response.json()["article_length"] == 420

    from app.models import UserSettings

    with world.session() as session:
        record = session.get(UserSettings, world.user_id)
        assert (record.daily_new_words, record.article_length) == (7, 420)


def test_non_admin_cannot_change_instance_settings(world) -> None:
    """DeepSeek and OCR are shared by every user, so they are admin-only."""
    for payload in (
        {"deepseek_api_key": "sk-attacker"},
        {"deepseek_base_url": "https://evil.example"},
        {"deepseek_model": "custom"},
        {"ocr_language": "ch"},
        {"ocr_use_gpu": True},
    ):
        response = world.client.put("/api/settings", json=payload)
        assert response.status_code == 403, (payload, response.status_code)


def test_admin_can_change_instance_settings(make_world) -> None:
    admin = make_world("settings-admin", role="admin")
    try:
        response = admin.client.put("/api/settings", json={"ocr_language": "ch"})
        assert response.status_code == 200
        assert response.json()["ocr_language"] == "ch"
        assert response.json()["can_manage_instance_settings"] is True
    finally:
        admin.client.__exit__(None, None, None)


def test_instance_level_backup_is_admin_only(world, make_world) -> None:
    """A backup covers the whole database, so it is an operator capability."""
    assert world.client.post("/api/settings/backup").status_code == 403

    admin = make_world("backup-admin", role="admin")
    try:
        assert admin.client.post("/api/settings/backup").status_code == 200
    finally:
        admin.client.__exit__(None, None, None)
