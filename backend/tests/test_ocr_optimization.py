from pathlib import Path

from PIL import Image


def _write_complete_model(model_dir: Path) -> None:
    model_dir.mkdir(parents=True)
    for filename in ("inference.yml", "inference.json", "inference.pdiparams"):
        (model_dir / filename).write_text("model-data", encoding="utf-8")


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


def test_incomplete_paddle_model_caches_are_removed_before_engine_creation(tmp_path: Path) -> None:
    from app.services.ocr.paddle import repair_incomplete_paddle_model_cache

    models_root = tmp_path / "official_models"
    complete = models_root / "PP-OCRv6_medium_det"
    missing_config = models_root / "PP-OCRv6_medium_rec"
    empty_parameters = models_root / "PP-LCNet_x1_0_doc_ori"
    _write_complete_model(complete)
    _write_complete_model(missing_config)
    (missing_config / "inference.yml").unlink()
    _write_complete_model(empty_parameters)
    (empty_parameters / "inference.pdiparams").write_text("", encoding="utf-8")
    (models_root / "download-metadata.json").write_text("{}", encoding="utf-8")

    removed = repair_incomplete_paddle_model_cache(tmp_path)

    assert removed == [empty_parameters, missing_config]
    assert complete.is_dir()
    assert (models_root / "download-metadata.json").is_file()
    assert not missing_config.exists()
    assert not empty_parameters.exists()


def test_missing_paddle_model_cache_needs_no_repair(tmp_path: Path) -> None:
    from app.services.ocr.paddle import repair_incomplete_paddle_model_cache

    assert repair_incomplete_paddle_model_cache(tmp_path) == []


def test_engine_repairs_incomplete_model_cache_before_constructing_paddle(monkeypatch, tmp_path: Path) -> None:
    import sys
    from types import SimpleNamespace

    from app.services.ocr.paddle import PaddleOCRProvider

    incomplete = tmp_path / "official_models" / "PP-OCRv6_medium_rec"
    incomplete.mkdir(parents=True)

    class FakePaddleOCR:
        def __init__(self, **_kwargs) -> None:
            assert not incomplete.exists()

    provider = PaddleOCRProvider()
    monkeypatch.setattr(provider, "_configure_model_cache", lambda: tmp_path)
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=FakePaddleOCR))

    assert isinstance(provider._get_engine(), FakePaddleOCR)


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
