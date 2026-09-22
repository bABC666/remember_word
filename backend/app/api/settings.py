from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, SessionDep, ensure_user_settings
from app.config import get_settings
from app.models import AppSetting, HistoryEvent
from app.schemas import SettingsUpdate
from app.services.backup import create_backup
from app.services.ocr import configure_paddle_environment

router = APIRouter(prefix="/api/settings", tags=["settings"])

#: Instance-level keys that are shared by everyone and therefore admin-only.
INSTANCE_KEYS = {"ocr_language", "ocr_use_gpu"}

DEFAULTS = {
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


def _instance_values(session) -> dict[str, str]:
    rows = session.scalars(select(AppSetting)).all()
    return {**DEFAULTS, **{row.key: row.value for row in rows}}


def _instance_health(settings_engine) -> tuple[bool, str]:
    if not settings_engine.ocr_enabled:
        return False, "当前实例已通过 VOCAB_ENABLE_OCR 关闭 OCR"
    try:
        configure_paddle_environment()
        import paddleocr  # noqa: F401

        return True, "PaddleOCR 已安装"
    except (ImportError, OSError) as error:
        return False, f"PaddleOCR 不可用：{error}"


@router.get("/onboarding")
def read_onboarding(user: CurrentUser, session: SessionDep) -> dict[str, bool]:
    """Whether **this user** has seen the introduction."""
    return {"seen": ensure_user_settings(session, user).onboarding_seen}


@router.post("/onboarding")
def mark_onboarding_seen(user: CurrentUser, session: SessionDep) -> dict[str, bool]:
    record = ensure_user_settings(session, user)
    record.onboarding_seen = True
    session.add(record)
    session.commit()
    return {"seen": True}


@router.get("")
def read_settings(user: CurrentUser, session: SessionDep) -> dict[str, object]:
    """The caller's own preferences plus read-only instance information.

    Per-user values come from ``user_settings``; shared instance values (OCR,
    the DeepSeek key) are reported but never mixed into the user's own settings.
    """
    config = get_settings()
    ai = config.ai_config()
    values = _instance_values(session)
    mine = ensure_user_settings(session, user)
    paddle_available, paddle_message = _instance_health(config)
    return {
        # per-user
        "daily_new_words": mine.daily_new_words,
        "article_length": mine.article_length,
        "onboarding_seen": mine.onboarding_seen,
        # instance-level, reported for context
        "deepseek_api_key_configured": bool(ai["api_key"]),
        "deepseek_api_key_masked": _mask(ai["api_key"]),
        "deepseek_base_url": ai["base_url"],
        "deepseek_model": ai["model"],
        "deepseek_model_display": describe_deepseek_model(ai["model"]),
        "ocr_language": values["ocr_language"],
        "ocr_use_gpu": values["ocr_use_gpu"].lower() == "true",
        "paddleocr_available": paddle_available,
        "paddleocr_message": paddle_message,
        # instance paths are operator information
        "data_directory": str(config.data_dir),
        "database_path": str(config.database_path),
        "backups_directory": str(config.backups_dir),
        "can_manage_instance_settings": user.is_admin,
    }


@router.put("")
def update_settings(
    payload: SettingsUpdate, user: CurrentUser, session: SessionDep
) -> dict[str, object]:
    """Update the caller's own preferences.

    Instance-level fields (DeepSeek key/base URL/model, OCR configuration) are
    refused with 403 for non-admins: they affect every user on the instance and
    must never be settable from a user's own settings screen.
    """
    changes = payload.model_dump(exclude_unset=True)

    instance_changes = {
        key: changes.pop(key)
        for key in ("deepseek_api_key", "deepseek_base_url", "deepseek_model", *INSTANCE_KEYS)
        if key in changes
    }
    if instance_changes and not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="DeepSeek 与 OCR 属于实例级配置，只有管理员可以修改",
        )

    mine = ensure_user_settings(session, user)
    if "daily_new_words" in changes and changes["daily_new_words"] is not None:
        mine.daily_new_words = int(changes["daily_new_words"])
    if "article_length" in changes and changes["article_length"] is not None:
        mine.article_length = int(changes["article_length"])
    session.add(mine)

    if instance_changes:
        _apply_instance_changes(session, instance_changes)

    session.commit()
    return read_settings(user, session)


def _apply_instance_changes(session, changes: dict[str, object]) -> None:
    config = get_settings()
    local_config = config.read_local_config()
    if changes.get("deepseek_api_key"):
        local_config["deepseek_api_key"] = changes["deepseek_api_key"]
    for key in ("deepseek_base_url", "deepseek_model"):
        if key in changes and changes[key] is not None:
            local_config[key] = changes[key]
    config.config_dir.mkdir(parents=True, exist_ok=True)
    temporary = config.local_config_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(local_config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(config.local_config_path)

    for key in INSTANCE_KEYS:
        if key not in changes or changes[key] is None:
            continue
        value = changes[key]
        row = session.get(AppSetting, key) or AppSetting(key=key)
        row.value = str(value).lower() if isinstance(value, bool) else str(value)
        session.add(row)


@router.post("/backup")
def manual_backup(admin: AdminUser, session: SessionDep) -> dict[str, object]:
    """Create an instance-level backup.

    A backup covers the whole database, so it is an operator capability rather
    than a personal one. Non-admins get 403; per the IDOR rules this is a
    capability refusal, not a resource lookup.
    """
    config = get_settings()
    name = f"manual-{datetime.now().astimezone().strftime('%Y-%m-%d-%H%M%S')}-vocab.db"
    path = create_backup(config.database_path, config.backups_dir, name=name)
    session.add(
        HistoryEvent(
            user_id=admin.id,
            event_type="manual_backup",
            entity_type="database",
            payload={"path": str(path), "by": admin.username},
        )
    )
    session.commit()
    return {"message": "备份已创建", "filename": path.name, "path": str(path)}
