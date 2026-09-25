from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

from app.config import get_settings
from app.services.ocr.base import OCRDocument, OCRLine, OCRProviderError

logger = logging.getLogger(__name__)


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


# --- PaddleX model cache repair -------------------------------------------
#
# PaddleX decides that a model is cached from ``os.path.exists`` on the model
# directory alone (``paddlex/inference/utils/official_models.py``,
# ``_get_model_local_path``), and its hosters download *into* an existing model
# directory when one is already there.  A directory left behind by an
# interrupted update therefore stays "cached" forever, and every later OCR run
# fails on the file that never arrived.
#
# The repair below is deliberately conservative.  It removes a cache only when
# it can prove the cache is unusable, only for directories PaddleX itself owns,
# and only while no other process can be downloading or reading them.

_MODEL_CONFIG_FILES = ("inference.yml", "inference.json")
#: Parameter artifacts PaddleX may load from a model directory.  The list is
#: wider than the Paddle format this application uses on purpose: an unknown
#: format must never turn a working model into a "repairable" one.
_MODEL_PARAMETER_GLOBS = ("*.pdiparams", "*.safetensors", "*.onnx")
#: The real PP-OCRv6 parameter files are 62 MB and 76 MB.  The floor only has to
#: catch obvious truncation -- a copy stopped by a full or unmounted disk -- so
#: it sits far below any real model while still rejecting placeholder files.
_MIN_PARAMETER_BYTES = 1 << 20
_MIN_CONFIG_BYTES = 16
#: PaddleX downloads into an existing model directory, so a recent write means a
#: download may still be running rather than a cache that will never finish.
_IN_PROGRESS_GRACE_SECONDS = 300.0
#: Bound for the scan that decides whether a directory is still being written.
#: Model directories are flat, so these limits are already generous.
_WRITE_SCAN_DEPTH = 4
_WRITE_SCAN_LIMIT = 4000
#: Suffix of a directory the repair renamed aside: PaddleX no longer sees it and
#: it is no longer a repair candidate.
_STALE_DIRECTORY_MARKER = ".__stale__"
#: Name shape of a directory PaddleX may create; rejects hidden and temp names.
_MODEL_DIRECTORY_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
#: PaddleX appends a format suffix to the directory name of non-Paddle packages.
_MODEL_FORMAT_SUFFIXES = ("_safetensors", "_onnx")
#: Files that show a directory holds (part of) a downloaded model.  An empty
#: directory counts as well: that is what an interrupted copy leaves behind.
_MODEL_ARTIFACT_NAMES = frozenset(
    {
        "inference.yml",
        "inference.json",
        "inference.pdmodel",
        "inference.pdiparams",
        "inference.pdiparams.info",
        "README.md",
        ".gitattributes",
        ".cache",
    }
)
_MODEL_ARTIFACT_SUFFIXES = (
    ".pdiparams",
    ".pdiparams.info",
    ".pdmodel",
    ".safetensors",
    ".onnx",
    ".json",
    ".yml",
    ".yaml",
)
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


@dataclass(frozen=True)
class ModelCacheRepair:
    """Outcome of one repair pass over ``official_models``.

    ``skipped`` records every *incomplete* cache that was deliberately left in
    place, with the reason, so a support report can explain why a stale-looking
    directory survived.  Complete caches and directories that are not PaddleX
    model caches are not listed at all.
    """

    removed: tuple[Path, ...] = ()
    skipped: tuple[tuple[Path, str], ...] = ()


def _strip_model_format_suffix(model_name: str) -> str:
    for suffix in _MODEL_FORMAT_SUFFIXES:
        if model_name.endswith(suffix):
            return model_name[: -len(suffix)]
    return model_name


@lru_cache(maxsize=1)
def _paddle_official_model_names() -> frozenset[str] | None:
    """The model directory names PaddleX publishes, or ``None`` when unknown.

    Repairing only names from PaddleX's own registry is what keeps an unrelated
    directory that happens to sit under ``official_models`` out of reach.  The
    import is best effort because the OCR extra is optional, and it is only
    attempted once a defective-looking directory has actually been found.
    """
    try:
        from paddlex.inference.utils.official_models import ALL_MODELS
    except (ImportError, OSError):
        # The OCR extra is optional: without PaddleX the name shape is all we have.
        return None
    return frozenset(ALL_MODELS)


@lru_cache(maxsize=1)
def _yaml_module() -> Any | None:
    try:
        import yaml
    except ImportError:  # pragma: no cover - PyYAML ships with the OCR extra
        return None
    return yaml


@lru_cache(maxsize=1)
def _filelock_module() -> Any | None:
    try:
        import filelock
    except ImportError:  # pragma: no cover - filelock ships with the OCR extra
        return None
    return filelock


def official_model_lock_path(cache_path: Path, model_name: str) -> Path:
    """Path of the cross-process lock PaddleX holds while downloading a model.

    Mirrors ``_official_model_download_lock_path`` in
    ``paddlex/inference/utils/official_models.py``: the key is
    ``sha256("\\0".join(model_names))`` below ``<cache>/locks/official_models``,
    and the repair asks about exactly one directory at a time.
    ``<cache>`` is ``PADDLE_PDX_CACHE_HOME``, which
    :func:`configure_paddle_environment` points at the directory passed here, so
    both sides agree on the same file.
    """
    key = hashlib.sha256(model_name.encode("utf-8")).hexdigest()
    return cache_path / "locks" / "official_models" / f"{key}.lock"


def _is_reparse_point(path: Path) -> bool:
    """Whether ``path`` is a symlink, junction or other reparse point.

    ``shutil.rmtree`` on a Windows junction would delete the contents of the
    *target*, so a model directory that is really a link elsewhere is never
    touched.
    """
    if path.is_symlink():
        return True
    try:
        status = path.lstat()
    except OSError:
        return False
    if getattr(status, "st_reparse_tag", 0):
        return True
    attributes = getattr(status, "st_file_attributes", 0)
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _config_file_defect(path: Path) -> str | None:
    """Why ``inference.yml`` or ``inference.json`` is unusable, or ``None``.

    A non-zero file is not enough.  A copy or download interrupted halfway
    leaves a truncated file that PaddleX accepts as a cache hit and then fails
    to parse, so the text still has to be a complete document.
    """
    try:
        if not path.is_file():
            return "缺失"
        if path.stat().st_size < _MIN_CONFIG_BYTES:
            return "为空或过短"
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "无法读取"
    if path.suffix == ".json":
        try:
            payload = json.loads(text)
        except ValueError:
            return "不是完整的 JSON"
        return None if isinstance(payload, dict) and payload else "内容不完整"
    yaml = _yaml_module()
    if yaml is None:
        return None  # not verifiable here; never call a model broken for that
    try:
        documents = list(yaml.compose_all(text))
    except yaml.YAMLError:
        return "不是完整的 YAML"
    if not documents or not isinstance(documents[0], yaml.MappingNode):
        return "不是完整的 YAML"
    return None


def _is_usable_parameter_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= _MIN_PARAMETER_BYTES
    except OSError:
        return False


def _model_cache_defect(model_dir: Path) -> str | None:
    """Why PaddleX cannot load this model directory, or ``None`` when it can."""
    for filename in _MODEL_CONFIG_FILES:
        defect = _config_file_defect(model_dir / filename)
        if defect is not None:
            return f"{filename} {defect}"
    parameters = [
        path for pattern in _MODEL_PARAMETER_GLOBS for path in model_dir.glob(pattern)
    ]
    if not parameters:
        return "缺少模型参数文件"
    if not any(_is_usable_parameter_file(path) for path in parameters):
        return "模型参数文件被截断"
    return None


def _is_complete_model_directory(model_dir: Path) -> bool:
    """Whether a PaddleX model directory holds everything needed to load it."""
    return _model_cache_defect(model_dir) is None


def _holds_model_materialization(model_dir: Path) -> bool:
    """Whether an incomplete directory still looks like a downloaded model.

    An empty directory returns ``True``: an interrupted copy of a model cache
    leaves exactly that behind.  A directory with unrelated contents does not.
    """
    try:
        children = list(model_dir.iterdir())
    except OSError as error:
        raise OCRProviderError(
            f"无法读取 PaddleOCR 模型缓存 {model_dir.name}：{error}"
        ) from error
    if not children:
        return True
    return any(
        child.name in _MODEL_ARTIFACT_NAMES or child.name.endswith(_MODEL_ARTIFACT_SUFFIXES)
        for child in children
    )


def _looks_like_model_directory_name(model_name: str) -> bool:
    """Name shape PaddleX model directories have; rejects hidden and temp names."""
    if _MODEL_DIRECTORY_NAME.fullmatch(model_name) is None:
        return False
    return _STALE_DIRECTORY_MARKER not in model_name


def _is_known_paddle_model(model_name: str) -> bool:
    """Whether PaddleX's own registry lists this model directory name."""
    known = _paddle_official_model_names()
    if known is None:
        return True  # registry unavailable; the name shape is all we have
    return _strip_model_format_suffix(model_name) in known


def _newest_write_time(model_dir: Path) -> float:
    """Newest mtime inside ``model_dir``, from a bounded scan.

    Only mtimes are read.  A model directory holds a handful of large files, and
    the bound keeps a pathological tree from turning a cache check into a walk.
    """
    newest = model_dir.stat().st_mtime
    stack: list[tuple[Path, int]] = [(model_dir, 0)]
    seen = 0
    while stack:
        current, depth = stack.pop()
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            seen += 1
            if seen > _WRITE_SCAN_LIMIT:
                return newest
            try:
                newest = max(newest, child.stat().st_mtime)
            except OSError:
                continue
            if depth + 1 < _WRITE_SCAN_DEPTH and child.is_dir() and not child.is_symlink():
                stack.append((child, depth + 1))
    return newest


def _incomplete_cache_skip_reason(
    model_dir: Path,
    defect: str,
    *,
    now: float,
    min_age_seconds: float,
) -> str | None:
    """Why an incomplete cache has to be left alone, or ``None`` to repair it."""
    if not _looks_like_model_directory_name(model_dir.name):
        return f"{defect}，但目录名不属于 PaddleX 模型缓存"
    if not _holds_model_materialization(model_dir):
        return f"{defect}，但目录内容不是模型文件"
    if not _is_known_paddle_model(model_dir.name):
        return f"{defect}，但名称不在 PaddleX 官方模型清单中"
    try:
        newest = _newest_write_time(model_dir)
    except OSError as error:
        return f"{defect}，但无法读取写入时间：{error}"
    if now - newest < min_age_seconds:
        return f"{defect}，但最近 {min_age_seconds:.0f} 秒内仍有写入，可能正在下载"
    return None


def _acquire_model_download_lock(cache_path: Path, model_name: str) -> Any | None:
    """Take PaddleX's download lock for ``model_name`` without blocking.

    Returns the lock when it was acquired and ``None`` when another process
    holds it, which is exactly the situation "a download is running" that the
    repair must never interrupt.
    """
    filelock = _filelock_module()
    if filelock is None:
        return None
    lock = filelock.FileLock(official_model_lock_path(cache_path, model_name))
    try:
        lock.acquire(timeout=0)
    except (filelock.Timeout, OSError):
        return None
    return lock


def _claim_directory_for_removal(model_dir: Path) -> Path | None:
    """Rename an incomplete cache aside so PaddleX stops treating it as cached.

    Renaming first is what keeps the repair atomic.  Windows refuses to rename a
    directory another process still has open, so a cache that is in use -- or
    that a download started writing to between the checks and here -- cannot be
    pulled out from under its owner.  The rename alone already restores the "not
    cached" state; deleting the claimed directory only reclaims disk.
    """
    claimed = model_dir.with_name(f"{model_dir.name}{_STALE_DIRECTORY_MARKER}{uuid4().hex}")
    try:
        os.rename(model_dir, claimed)
    except OSError:
        return None
    return claimed


def repair_incomplete_paddle_model_cache(
    cache_path: Path,
    *,
    now: float | None = None,
    min_age_seconds: float = _IN_PROGRESS_GRACE_SECONDS,
) -> ModelCacheRepair:
    """Remove unusable PaddleX model caches so PaddleOCR downloads them again.

    Only immediate directories under ``official_models`` are ever considered, so
    the ``locks``, ``temp`` and ``func_ret`` directories PaddleX keeps next to it
    are out of scope by construction.  A candidate is removed only when it is
    provably incomplete *and* provably idle: a PaddleX official-model name, not a
    link, holding model files, untouched for ``min_age_seconds``, and with
    PaddleX's own download lock free.  Everything else is reported in
    ``ModelCacheRepair.skipped`` instead of being deleted.

    ``now`` exists so the "is a download still running" decision is
    deterministic under test.
    """
    models_root = cache_path / "official_models"
    if not models_root.is_dir():
        return ModelCacheRepair()
    moment = time.time() if now is None else now
    try:
        candidates = sorted(path for path in models_root.iterdir() if path.is_dir())
    except OSError as error:
        raise OCRProviderError(f"无法检查 PaddleOCR 模型缓存：{error}") from error

    removed: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    for model_dir in candidates:
        try:
            defect = _model_cache_defect(model_dir)
        except OSError as error:
            skipped.append((model_dir, f"无法检查：{error}"))
            continue
        if defect is None:
            continue
        if _is_reparse_point(model_dir):
            skipped.append((model_dir, f"{defect}，但目录是指向别处的链接"))
            continue
        reason = _incomplete_cache_skip_reason(
            model_dir, defect, now=moment, min_age_seconds=min_age_seconds
        )
        if reason is not None:
            skipped.append((model_dir, reason))
            continue

        lock = None
        if _filelock_module() is not None:
            lock = _acquire_model_download_lock(cache_path, model_dir.name)
            if lock is None:
                skipped.append((model_dir, f"{defect}，但 PaddleX 正在下载该模型（下载锁被占用）"))
                continue
        try:
            if _model_cache_defect(model_dir) is None:
                continue  # a concurrent download completed it while we waited
            claimed = _claim_directory_for_removal(model_dir)
        except OSError as error:
            skipped.append((model_dir, f"无法在下载锁内复查：{error}"))
            continue
        finally:
            if lock is not None:
                lock.release()
        if claimed is None:
            skipped.append((model_dir, f"{defect}，但目录正被其它进程使用，无法改名"))
            continue
        try:
            shutil.rmtree(claimed)
        except OSError as error:
            # The original path is gone, so PaddleOCR already re-downloads; only
            # reclaiming the disk failed.  Leaving the claimed directory behind
            # is visible and harmless, and it is not a repair candidate again.
            logger.warning("PaddleOCR 模型缓存 %s 已失效但未能删除：%s", claimed.name, error)
        removed.append(model_dir)

    if removed:
        logger.warning(
            "已删除 %d 个不完整的 PaddleOCR 模型缓存目录，PaddleOCR 会重新下载：%s",
            len(removed),
            "、".join(path.name for path in removed),
        )
    return ModelCacheRepair(removed=tuple(removed), skipped=tuple(skipped))


class PaddleOCRProvider:
    name = "paddleocr"

    def __init__(self, language: str = "en", use_gpu: bool = False) -> None:
        self.language = language
        self.use_gpu = use_gpu
        self._engine: Any | None = None

    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        cache_path = self._configure_model_cache()
        repair_incomplete_paddle_model_cache(cache_path)
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

    def _configure_model_cache(self) -> Path:
        # Paddle 3.3 on Windows currently fails on the default oneDNN/PIR path for
        # OCR detection models. The plain CPU executor is stable and deterministic.
        return configure_paddle_environment()

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
