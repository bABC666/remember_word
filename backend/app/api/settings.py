from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import AppSetting, HistoryEvent
from app.schemas import SettingsUpdate
from app.services.backup import create_backup
from app.services.ocr import configure_paddle_environment

router = APIRouter(prefix="/api/settings", tags=["settings"])
DEFAULTS = {
    "daily_new_words": "15",
    "article_length": "650",
    "ocr_language": "en",
    "ocr_use_gpu": "false",
}


def describe_deepseek_model(model: str) -> str:
    return {
        "deepseek-flash": "DeepSeek V4.1-Flash",
        "deepseek-v4-pro": "DeepSeek V4-Pro-0813",
    }.get(model, f"自定义模型（{model}）")


def _mask(secret: str) -> str:
    if not secret:
        return ""
    return "•" * max(8, min(16, len(secret) - 4)) + secret[-4:]


def _values(session: Session) -> dict[str, str]:
    rows = session.scalars(select(AppSetting)).all()
    return {**DEFAULTS, **{row.key: row.value for row in rows}}


@router.get("/onboarding")
def read_onboarding(session: Session = Depends(get_session)) -> dict[str, bool]:
    row = session.get(AppSetting, "onboarding_seen")
    return {"seen": row is not None and row.value.lower() == "true"}


@router.post("/onboarding")
def mark_onboarding_seen(session: Session = Depends(get_session)) -> dict[str, bool]:
    row = session.get(AppSetting, "onboarding_seen") or AppSetting(key="onboarding_seen")
    row.value = "true"
    session.add(row)
    session.commit()
    return {"seen": True}


@router.get("")
def read_settings(session: Session = Depends(get_session)) -> dict[str, object]:
    settings = get_settings()
    ai = settings.ai_config()
    values = _values(session)
    if not settings.ocr_enabled:
        paddle_available = False
        paddle_message = "当前实例已通过 VOCAB_ENABLE_OCR 关闭 OCR"
    else:
        try:
            configure_paddle_environment()
            import paddleocr  # noqa: F401

            paddle_available = True
            paddle_message = "PaddleOCR 已安装"
        except (ImportError, OSError) as error:
            paddle_available = False
            paddle_message = f"PaddleOCR 不可用：{error}"
    return {
        "deepseek_api_key_configured": bool(ai["api_key"]),
        "deepseek_api_key_masked": _mask(ai["api_key"]),
        "deepseek_base_url": ai["base_url"],
        "deepseek_model": ai["model"],
        "deepseek_model_display": describe_deepseek_model(ai["model"]),
        "daily_new_words": int(values["daily_new_words"]),
        "article_length": int(values["article_length"]),
        "ocr_language": values["ocr_language"],
        "ocr_use_gpu": values["ocr_use_gpu"].lower() == "true",
        "paddleocr_available": paddle_available,
        "paddleocr_message": paddle_message,
        "data_directory": str(settings.data_dir),
        "database_path": str(settings.database_path),
        "backups_directory": str(settings.backups_dir),
    }


@router.put("")
def update_settings(
    payload: SettingsUpdate, session: Session = Depends(get_session)
) -> dict[str, object]:
    settings = get_settings()
    current_config = settings.read_local_config()
    changes = payload.model_dump(exclude_unset=True)
    if "deepseek_api_key" in changes:
        value = changes.pop("deepseek_api_key")
        if value:
            current_config["deepseek_api_key"] = value
    for key in ("deepseek_base_url", "deepseek_model"):
        if key in changes and changes[key] is not None:
            current_config[key] = changes.pop(key)
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    temporary = settings.local_config_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(current_config, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(settings.local_config_path)
    for key, value in changes.items():
        row = session.get(AppSetting, key) or AppSetting(key=key)
        row.value = str(value).lower() if isinstance(value, bool) else str(value)
        session.add(row)
    session.commit()
    return read_settings(session)


@router.post("/backup")
def manual_backup(session: Session = Depends(get_session)) -> dict[str, object]:
    settings = get_settings()
    name = f"manual-{datetime.now().astimezone().strftime('%Y-%m-%d-%H%M%S')}-vocab.db"
    path = create_backup(settings.database_path, settings.backups_dir, name=name)
    session.add(
        HistoryEvent(
            event_type="manual_backup", entity_type="database", payload={"path": str(path)}
        )
    )
    session.commit()
    return {"message": "备份已创建", "filename": path.name, "path": str(path)}
