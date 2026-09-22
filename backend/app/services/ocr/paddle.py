from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

from app.config import get_settings
from app.services.ocr.base import OCRDocument, OCRLine, OCRProviderError


@contextmanager
def prepared_ocr_image(image_path: Path, max_side: int = 2200):
    """Yield an OCR-sized copy for very large photos while preserving the source file."""
    temporary: Path | None = None
    with Image.open(image_path) as source:
        if max(source.size) <= max_side:
            yield image_path
            return
        resized = source.convert("RGB")
        resized.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        temporary = get_settings().ocr_temp_dir / f"{image_path.stem}-{uuid4().hex}.jpg"
        temporary.parent.mkdir(parents=True, exist_ok=True)
        resized.save(temporary, "JPEG", quality=94, optimize=True)
    try:
        yield temporary
    finally:
        temporary.unlink(missing_ok=True)


def configure_paddle_environment() -> Path:
    """Keep Paddle runtime state in the app data directory before importing PaddleX."""
    os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    configured = os.getenv("PADDLE_PDX_CACHE_HOME")
    if configured:
        return Path(configured)

    target = get_settings().data_dir / "ocr-models"
    target.mkdir(parents=True, exist_ok=True)
    try:
        str(target).encode("ascii")
        cache_path = target
    except UnicodeEncodeError:
        if os.name != "nt":
            cache_path = target
        else:
            digest = hashlib.sha256(str(target).encode("utf-8")).hexdigest()[:10]
            cache_path = Path(tempfile.gettempdir()) / f"shici-paddlex-{digest}"
            if not cache_path.exists():
                result = subprocess.run(
                    ["cmd.exe", "/c", "mklink", "/J", str(cache_path), str(target)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    raise OCRProviderError(
                        "无法为 PaddleOCR 创建 ASCII 模型缓存入口：" + result.stderr.strip()
                    )
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(cache_path)
    return cache_path


class PaddleOCRProvider:
    name = "paddleocr"

    def __init__(self, language: str = "en", use_gpu: bool = False) -> None:
        self.language = language
        self.use_gpu = use_gpu
        self._engine: Any | None = None

    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        self._configure_model_cache()
        try:
            from paddleocr import PaddleOCR
        except (ImportError, OSError) as error:
            raise OCRProviderError(
                "PaddleOCR 尚未安装或当前 Python 环境不兼容。请安装 backend[ocr]，"
                "或按 README 使用独立 OCR 环境。"
            ) from error
        try:
            self._engine = PaddleOCR(
                lang=self.language,
                device="gpu:0" if self.use_gpu else "cpu",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
        except TypeError:
            self._engine = PaddleOCR(
                lang=self.language, use_angle_cls=True, show_log=False, use_gpu=self.use_gpu
            )
        except Exception as error:
            raise OCRProviderError(f"PaddleOCR 初始化失败：{error}") from error
        return self._engine

    def _configure_model_cache(self) -> None:
        # Paddle 3.3 on Windows currently fails on the default oneDNN/PIR path for
        # OCR detection models. The plain CPU executor is stable and deterministic.
        configure_paddle_environment()

    def extract(self, image_path: Path) -> OCRDocument:
        engine = self._get_engine()
        try:
            with prepared_ocr_image(image_path) as prepared:
                if hasattr(engine, "predict"):
                    result = list(engine.predict(str(prepared)))
                    return self._from_v3(result)
                return self._from_legacy(engine.ocr(str(prepared), cls=True))
        except OCRProviderError:
            raise
        except Exception as error:
            raise OCRProviderError(f"PaddleOCR 识别失败：{error}") from error

    def _from_v3(self, result: list[Any]) -> OCRDocument:
        lines: list[OCRLine] = []
        raw: list[object] = []
        for page in result:
            payload = page.json if hasattr(page, "json") else page
            if callable(payload):
                payload = payload()
            if isinstance(payload, str):
                import json

                payload = json.loads(payload)
            raw.append(payload)
            value = payload.get("res", payload) if isinstance(payload, dict) else {}
            texts = value.get("rec_texts", [])
            scores = value.get("rec_scores", [])
            boxes = value.get("rec_polys", value.get("dt_polys", []))
            for index, text in enumerate(texts):
                score = float(scores[index]) if index < len(scores) else 0.0
                box = (
                    boxes[index].tolist()
                    if index < len(boxes) and hasattr(boxes[index], "tolist")
                    else (boxes[index] if index < len(boxes) else [])
                )
                lines.append(OCRLine(text=str(text), confidence=score, box=box))
        return OCRDocument(provider=self.name, lines=lines, raw=raw)

    def _from_legacy(self, result: Any) -> OCRDocument:
        lines: list[OCRLine] = []
        for page in result or []:
            for item in page or []:
                if len(item) < 2:
                    continue
                box, recognized = item[0], item[1]
                lines.append(
                    OCRLine(text=str(recognized[0]), confidence=float(recognized[1]), box=box)
                )
        return OCRDocument(provider=self.name, lines=lines, raw=result or [])


@lru_cache(maxsize=8)
def get_paddle_provider(language: str = "en", use_gpu: bool = False) -> PaddleOCRProvider:
    """Keep one lazily initialized Paddle engine per runtime configuration."""
    return PaddleOCRProvider(language=language, use_gpu=use_gpu)
