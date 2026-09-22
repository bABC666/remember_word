from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_dir: Path
    database_path: Path
    uploads_dir: Path
    backups_dir: Path
    ocr_temp_dir: Path
    config_dir: Path
    frontend_dist: Path

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def local_config_path(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def ocr_enabled(self) -> bool:
        """PaddleOCR is a local-only capability.

        The cloud deployment must be able to start without Paddle installed, so
        every OCR entry point is guarded by this flag instead of relying on the
        import failing somewhere deep in the stack.
        """
        return os.getenv("VOCAB_ENABLE_OCR", "true").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.uploads_dir,
            self.backups_dir,
            self.ocr_temp_dir,
            self.config_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def read_local_config(self) -> dict[str, object]:
        if not self.local_config_path.exists():
            return {}
        try:
            return json.loads(self.local_config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def ai_config(self) -> dict[str, str]:
        local = self.read_local_config()
        configured_model = os.getenv(
            "DEEPSEEK_MODEL", str(local.get("deepseek_model", "deepseek-flash"))
        ).strip()
        if configured_model in {"deepseek-chat", "deepseek-v4-flash"}:
            configured_model = "deepseek-flash"
        return {
            "api_key": os.getenv("DEEPSEEK_API_KEY", str(local.get("deepseek_api_key", ""))),
            "base_url": os.getenv(
                "DEEPSEEK_BASE_URL",
                str(local.get("deepseek_base_url", "https://api.deepseek.com")),
            ),
            "model": configured_model or "deepseek-flash",
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    root = _project_root()
    data_dir = Path(os.getenv("VOCAB_DATA_DIR", str(root / "data"))).resolve()
    settings = Settings(
        project_root=root,
        data_dir=data_dir,
        database_path=data_dir / "vocab.db",
        uploads_dir=data_dir / "uploads",
        backups_dir=data_dir / "backups",
        ocr_temp_dir=data_dir / "ocr-temp",
        config_dir=data_dir / "config",
        frontend_dist=root / "frontend" / "dist",
    )
    settings.ensure_directories()
    return settings
