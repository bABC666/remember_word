import json


def test_deepseek_default_uses_current_flash_model(monkeypatch, tmp_path) -> None:
    from app.config import get_settings

    monkeypatch.setenv("VOCAB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    get_settings.cache_clear()

    assert get_settings().ai_config()["model"] == "deepseek-flash"


def test_legacy_deepseek_chat_config_maps_to_current_flash(monkeypatch, tmp_path) -> None:
    from app.config import get_settings

    data_dir = tmp_path / "data"
    config_dir = data_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "settings.json").write_text(
        json.dumps({"deepseek_model": "deepseek-chat"}), encoding="utf-8"
    )
    monkeypatch.setenv("VOCAB_DATA_DIR", str(data_dir))
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    get_settings.cache_clear()

    assert get_settings().ai_config()["model"] == "deepseek-flash"


def test_model_display_names_are_explicit() -> None:
    from app.api.settings import describe_deepseek_model

    assert describe_deepseek_model("deepseek-flash") == "DeepSeek V4.1-Flash"
    assert describe_deepseek_model("deepseek-v4-pro") == "DeepSeek V4-Pro-0813"
    assert describe_deepseek_model("custom-model") == "自定义模型（custom-model）"
