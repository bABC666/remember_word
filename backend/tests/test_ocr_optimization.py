from pathlib import Path

import pytest
from PIL import Image


def test_paddle_provider_factory_reuses_engine_owner() -> None:
    from app.services.ocr.paddle import get_paddle_provider

    get_paddle_provider.cache_clear()
    first = get_paddle_provider("en", False)
    second = get_paddle_provider("en", False)
    different = get_paddle_provider("ch", False)

    assert first is second
    assert different is not first


def test_large_ocr_input_is_downscaled_without_changing_source(tmp_path: Path) -> None:
    from app.services.ocr.paddle import prepared_ocr_image

    source = tmp_path / "phone-photo.png"
    Image.new("RGB", (4400, 2200), "white").save(source)

    with prepared_ocr_image(source, max_side=2200) as prepared:
        assert prepared != source
        assert prepared.exists()
        with Image.open(prepared) as image:
            assert image.size == (2200, 1100)
        with Image.open(source) as original:
            assert original.size == (4400, 2200)

    assert not prepared.exists()
    assert source.exists()


def test_small_ocr_input_uses_original_file(tmp_path: Path) -> None:
    from app.services.ocr.paddle import prepared_ocr_image

    source = tmp_path / "page.png"
    Image.new("RGB", (1200, 900), "white").save(source)

    with prepared_ocr_image(source, max_side=2200) as prepared:
        assert prepared == source


def test_batch_ocr_reuses_successful_images_and_processes_only_missing(session, tmp_path) -> None:
    from app.models import ImportBatch, ImportImage
    from app.services.imports import process_batch_ocr
    from app.services.ocr.base import OCRDocument, OCRLine

    existing_path = tmp_path / "existing.png"
    missing_path = tmp_path / "missing.png"
    Image.new("RGB", (100, 100), "white").save(existing_path)
    Image.new("RGB", (100, 100), "white").save(missing_path)
    batch = ImportBatch(status="uploaded", stage="upload")
    batch.images.extend(
        [
            ImportImage(
                original_name="existing.png",
                file_path=str(existing_path),
                sha256="a" * 64,
                ocr_text="retain v. 保留",
                ocr_raw_json={"provider": "paddleocr", "lines": [{"text": "retain v. 保留"}]},
            ),
            ImportImage(
                original_name="missing.png",
                file_path=str(missing_path),
                sha256="b" * 64,
            ),
        ]
    )
    session.add(batch)
    session.commit()

    class FakeProvider:
        name = "paddleocr"

        def __init__(self) -> None:
            self.paths: list[Path] = []

        def extract(self, path: Path) -> OCRDocument:
            self.paths.append(path)
            return OCRDocument(
                provider=self.name,
                lines=[OCRLine(text="derive v. 获得", confidence=0.99, box=[])],
                raw={"ok": True},
            )

    provider = FakeProvider()
    process_batch_ocr(session, batch, provider)

    assert provider.paths == [missing_path]
    assert batch.status == "ocr_complete"
    assert "retain v. 保留" in batch.raw_ocr_text
    assert "derive v. 获得" in batch.raw_ocr_text


@pytest.mark.parametrize("language", ["en", "ch"])
def test_mobile_profile_loads_small_models(monkeypatch, tmp_path, language) -> None:
    import sys
    from types import SimpleNamespace

    from app.services.ocr import paddle

    monkeypatch.setenv("VOCAB_OCR_PROFILE", "mobile")
    monkeypatch.setattr(paddle, "configure_paddle_environment", lambda: tmp_path)
    monkeypatch.setattr(paddle, "repair_incomplete_paddle_model_cache", lambda _path: None)
    options = {}

    def create_engine(**kwargs):
        options.update(kwargs)
        return object()

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=create_engine))
    provider = paddle.PaddleOCRProvider(language)
    assert provider._get_engine() is provider._get_engine()
    assert options["text_detection_model_name"] == "PP-OCRv5_mobile_det"
    expected = "en_PP-OCRv5_mobile_rec" if language == "en" else "PP-OCRv5_mobile_rec"
    assert options["text_recognition_model_name"] == expected
    assert options["cpu_threads"] == 1
    assert options["text_recognition_batch_size"] == 1
    assert options["text_det_limit_type"] == "max"
    assert options["text_det_limit_side_len"] == 1024
    assert options["enable_mkldnn"] is False
    assert options["device"] == "cpu"
    assert "lang" not in options


def test_mobile_profile_does_not_fall_back_to_larger_defaults(monkeypatch, tmp_path) -> None:
    import sys
    from types import SimpleNamespace

    from app.services.ocr import paddle

    monkeypatch.setenv("VOCAB_OCR_PROFILE", "mobile")
    monkeypatch.setattr(paddle, "configure_paddle_environment", lambda: tmp_path)
    monkeypatch.setattr(paddle, "repair_incomplete_paddle_model_cache", lambda _path: None)
    calls = []

    def incompatible_engine(**kwargs):
        calls.append(kwargs)
        raise TypeError("unsupported mobile option")

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=incompatible_engine))
    with pytest.raises(paddle.OCRProviderError, match="初始化失败"):
        paddle.PaddleOCRProvider()._get_engine()
    assert len(calls) == 1


def test_ocr_rejects_concurrent_inference_and_recovers(monkeypatch, tmp_path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from app.services.ocr import paddle

    source = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(source)
    started, release = Event(), Event()

    class BlockingEngine:
        def predict(self, _path):
            started.set()
            assert release.wait(5)
            return [{"rec_texts": ["hello"], "rec_scores": [0.99]}]

    provider = paddle.PaddleOCRProvider()
    provider._engine = BlockingEngine()
    second = paddle.PaddleOCRProvider("ch")
    second._engine = BlockingEngine()
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(provider.extract, source)
        try:
            assert started.wait(5)
            with pytest.raises(paddle.OCRProviderError, match="稍后重试"):
                second.extract(source)
        finally:
            release.set()
        assert first.result(timeout=5).text == "hello"
    assert second.extract(source).text == "hello"


def test_ocr_initialization_failure_releases_inference_lock(monkeypatch, tmp_path) -> None:
    from app.services.ocr import paddle

    source = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(source)
    provider = paddle.PaddleOCRProvider()
    monkeypatch.setenv("VOCAB_OCR_PROFILE", "invalid")
    with pytest.raises(paddle.OCRProviderError, match="VOCAB_OCR_PROFILE"):
        provider.extract(source)
    monkeypatch.setenv("VOCAB_OCR_PROFILE", "default")

    class WorkingEngine:
        def predict(self, _path):
            return [{"rec_texts": ["retry"], "rec_scores": [0.99]}]

    provider._engine = WorkingEngine()
    assert provider.extract(source).text == "retry"
