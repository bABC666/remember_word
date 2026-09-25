"""Isolation and concurrency contract of the PaddleX model cache repair.

Every test builds its own cache tree under ``tmp_path``.  The real cache in
``data/ocr-models`` is never handed to the repair and never read: the point of
these tests is that a broken or busy cache is repaired *without* collateral
damage, so the suite must not need a real model to prove it.

Two failure modes are covered on purpose, because both were found in review of
the first repair implementation:

* an incomplete directory was removed whether or not it belonged to PaddleX and
  whether or not a download was still writing into it;
* a file that existed with a non-zero size counted as complete, so a truncated
  ``inference.pdiparams`` or ``inference.json`` still looked like a valid cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app.services.ocr import paddle

#: Mirrors the floor the repair uses; a "complete" fixture has to clear it.
PARAMETER_BYTES = 1 << 20
#: The three names the fixtures use, pinned as if PaddleX's registry listed them.
FIXTURE_MODELS = frozenset({"PP-OCRv6_medium_det", "PP-OCRv6_medium_rec", "PP-LCNet_x1_0_doc_ori"})


def _write_model(model_dir: Path, *, parameter_bytes: int = PARAMETER_BYTES) -> Path:
    """Write a model directory shaped like a real PP-OCRv6 download."""
    model_dir.mkdir(parents=True)
    (model_dir / "inference.yml").write_text(
        "Global:\n  model_name: fixture\nPreprocess:\n  - DecodeImage: null\n",
        encoding="utf-8",
    )
    (model_dir / "inference.json").write_text(
        json.dumps({"Global": {"model_name": "fixture"}, "Preprocess": []}),
        encoding="utf-8",
    )
    (model_dir / "inference.pdiparams").write_bytes(b"\0" * parameter_bytes)
    return model_dir


def _make_idle(root: Path, *, seconds: int = 3600) -> None:
    """Backdate a fixture tree so the in-progress grace window does not apply.

    Production decides "a download may still be running" from the newest write
    time; fixtures are written a moment ago, so they have to look older than the
    window before a repair is allowed to touch them.
    """
    moment = time.time() - seconds
    for path in sorted(root.rglob("*"), reverse=True):
        os.utime(path, (moment, moment))
    os.utime(root, (moment, moment))


def _models_root(tmp_path: Path) -> Path:
    models = tmp_path / "official_models"
    models.mkdir(exist_ok=True)
    return models


@pytest.fixture
def pinned_registry(monkeypatch: pytest.MonkeyPatch) -> frozenset[str]:
    """Pin PaddleX's model registry so results do not depend on the OCR extra."""
    monkeypatch.setattr(paddle, "_paddle_official_model_names", lambda: FIXTURE_MODELS)
    return FIXTURE_MODELS


def test_complete_model_caches_are_kept(tmp_path: Path, pinned_registry: frozenset[str]) -> None:
    models = _models_root(tmp_path)
    detection = _write_model(models / "PP-OCRv6_medium_det")
    recognition = _write_model(models / "PP-OCRv6_medium_rec")
    _make_idle(tmp_path)

    repair = paddle.repair_incomplete_paddle_model_cache(tmp_path)

    assert repair.removed == ()
    assert repair.skipped == ()
    assert detection.is_dir()
    assert recognition.is_dir()
    assert (recognition / "inference.yml").is_file()


def test_incomplete_model_caches_are_removed(tmp_path: Path, pinned_registry: frozenset[str]) -> None:
    models = _models_root(tmp_path)
    complete = _write_model(models / "PP-OCRv6_medium_det")
    missing_config = _write_model(models / "PP-OCRv6_medium_rec")
    (missing_config / "inference.yml").unlink()
    empty_directory = models / "PP-LCNet_x1_0_doc_ori"
    empty_directory.mkdir()
    (models / "download-metadata.json").write_text("{}", encoding="utf-8")
    _make_idle(tmp_path)

    repair = paddle.repair_incomplete_paddle_model_cache(tmp_path)

    assert repair.removed == (empty_directory, missing_config)
    assert complete.is_dir()
    assert (models / "download-metadata.json").is_file()
    assert not missing_config.exists()
    assert not empty_directory.exists()


def test_truncated_parameter_file_is_not_a_complete_cache(
    tmp_path: Path, pinned_registry: frozenset[str]
) -> None:
    """A non-zero but truncated parameter file must not count as a valid cache."""
    models = _models_root(tmp_path)
    truncated = _write_model(models / "PP-OCRv6_medium_rec", parameter_bytes=4096)
    _make_idle(tmp_path)

    repair = paddle.repair_incomplete_paddle_model_cache(tmp_path)

    assert repair.removed == (truncated,)
    assert not truncated.exists()


def test_truncated_json_config_is_not_a_complete_cache(
    tmp_path: Path, pinned_registry: frozenset[str]
) -> None:
    models = _models_root(tmp_path)
    truncated = _write_model(models / "PP-OCRv6_medium_rec")
    (truncated / "inference.json").write_text('{"Global": {"model_name": "f', encoding="utf-8")
    _make_idle(tmp_path)

    repair = paddle.repair_incomplete_paddle_model_cache(tmp_path)

    assert repair.removed == (truncated,)
    assert not truncated.exists()


def test_truncated_yaml_config_is_not_a_complete_cache(
    tmp_path: Path, pinned_registry: frozenset[str]
) -> None:
    models = _models_root(tmp_path)
    truncated = _write_model(models / "PP-OCRv6_medium_det")
    (truncated / "inference.yml").write_text("Global:\n  model_name: fixture\n  - brok", encoding="utf-8")
    _make_idle(tmp_path)

    repair = paddle.repair_incomplete_paddle_model_cache(tmp_path)

    assert repair.removed == (truncated,)
    assert not truncated.exists()


def test_unrelated_directory_with_contents_is_never_removed(
    tmp_path: Path, pinned_registry: frozenset[str]
) -> None:
    """Only model caches are repaired; a neighbour that is not one is left alone."""
    models = _models_root(tmp_path)
    uploads = models / "uploads"
    uploads.mkdir()
    (uploads / "page-01.jpg").write_bytes(b"jpeg")
    hidden = models / ".cache"
    hidden.mkdir()
    (hidden / "state.json").write_text("{}", encoding="utf-8")
    _make_idle(tmp_path)

    repair = paddle.repair_incomplete_paddle_model_cache(tmp_path)

    assert repair.removed == ()
    assert (uploads / "page-01.jpg").is_file()
    assert (hidden / "state.json").is_file()


def test_model_directory_outside_paddlex_registry_is_skipped(
    tmp_path: Path, pinned_registry: frozenset[str]
) -> None:
    models = _models_root(tmp_path)
    unknown = _write_model(models / "my-old-model-copy")
    (unknown / "inference.yml").unlink()
    _make_idle(tmp_path)

    repair = paddle.repair_incomplete_paddle_model_cache(tmp_path)

    assert repair.removed == ()
    assert unknown.is_dir()
    assert "官方模型清单" in repair.skipped[0][1]


def test_unreadable_registry_leaves_every_name_shaped_directory_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without PaddleX's own model list, no name shape authorises a deletion.

    Falling back to the directory name shape would delete any directory that
    happens to be named like a model, which is precisely the guess that made the
    first implementation dangerous.  An unreadable registry has to skip the
    repair and say why.
    """
    monkeypatch.setattr(paddle, "_paddle_official_model_names", lambda: None)
    models = _models_root(tmp_path)
    empty = models / "PP-OCRv6_medium_det"
    empty.mkdir()
    truncated = _write_model(models / "PP-OCRv6_medium_rec", parameter_bytes=1024)
    unrelated_name = _write_model(models / "my-old-model-copy")
    (unrelated_name / "inference.yml").unlink()
    _make_idle(tmp_path)

    repair = paddle.repair_incomplete_paddle_model_cache(tmp_path)

    assert repair.removed == ()
    assert empty.is_dir()
    assert (truncated / "inference.pdiparams").stat().st_size == 1024
    assert unrelated_name.is_dir()
    assert {path for path, _reason in repair.skipped} == {empty, truncated, unrelated_name}
    assert all("无法读取 PaddleX 官方模型清单" in reason for _path, reason in repair.skipped)

    # With a readable registry the two official names are repaired and the third
    # is still refused, so the skip above is the missing list, not a bad fixture.
    monkeypatch.setattr(paddle, "_paddle_official_model_names", lambda: FIXTURE_MODELS)
    repaired = paddle.repair_incomplete_paddle_model_cache(tmp_path)
    assert repaired.removed == (empty, truncated)
    assert unrelated_name.is_dir()


def test_registry_reader_reports_an_unusable_list_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty registry is a broken registry, not a registry of no models."""
    import sys
    from types import SimpleNamespace

    module = "paddlex.inference.utils.official_models"
    monkeypatch.setitem(sys.modules, module, SimpleNamespace(ALL_MODELS=[]))
    paddle._paddle_official_model_names.cache_clear()
    try:
        assert paddle._paddle_official_model_names() is None

        monkeypatch.setitem(
            sys.modules, module, SimpleNamespace(ALL_MODELS=["PP-OCRv6_medium_rec"])
        )
        paddle._paddle_official_model_names.cache_clear()
        assert paddle._paddle_official_model_names() == frozenset({"PP-OCRv6_medium_rec"})
    finally:
        paddle._paddle_official_model_names.cache_clear()


def test_registry_reader_reports_a_failed_import_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PaddleX import that fails must return ``None``, never raise.

    ``None`` in ``sys.modules`` is the documented way to make an import fail, and
    it is the state a machine without the OCR extra is in.
    """
    import sys

    monkeypatch.setitem(sys.modules, "paddlex.inference.utils.official_models", None)
    paddle._paddle_official_model_names.cache_clear()
    try:
        assert paddle._paddle_official_model_names() is None
    finally:
        paddle._paddle_official_model_names.cache_clear()


def test_only_official_models_is_scanned(tmp_path: Path, pinned_registry: frozenset[str]) -> None:
    """``locks``, ``temp`` and ``func_ret`` sit next to ``official_models``."""
    models = _models_root(tmp_path)
    broken_official = _write_model(models / "PP-OCRv6_medium_rec")
    (broken_official / "inference.yml").unlink()
    siblings: dict[str, Path] = {}
    for name in ("temp", "func_ret", "locks"):
        directory = _write_model(tmp_path / name / "PP-OCRv6_medium_rec")
        (directory / "inference.yml").unlink()
        siblings[name] = directory
    _make_idle(tmp_path)

    repair = paddle.repair_incomplete_paddle_model_cache(tmp_path)

    assert repair.removed == (broken_official,)
    assert not broken_official.exists()
    assert all(directory.is_dir() for directory in siblings.values())


def test_repair_without_official_models_root_is_a_noop(tmp_path: Path) -> None:
    assert paddle.repair_incomplete_paddle_model_cache(tmp_path).removed == ()


def test_recently_written_incomplete_cache_is_left_alone(
    tmp_path: Path, pinned_registry: frozenset[str]
) -> None:
    """PaddleX downloads into an existing model directory, so fresh means busy."""
    models = _models_root(tmp_path)
    busy = _write_model(models / "PP-OCRv6_medium_rec")
    (busy / "inference.yml").unlink()

    still_busy = paddle.repair_incomplete_paddle_model_cache(tmp_path)
    assert still_busy.removed == ()
    assert busy.is_dir()
    assert "正在下载" in still_busy.skipped[0][1]
    assert still_busy.skipped[0][0] == busy

    _make_idle(tmp_path)
    repaired = paddle.repair_incomplete_paddle_model_cache(tmp_path)
    assert repaired.removed == (busy,)
    assert not busy.exists()


def test_download_leftover_written_moments_ago_defers_the_repair(
    tmp_path: Path, pinned_registry: frozenset[str]
) -> None:
    """A nested ``.incomplete`` file is the strongest sign of a live download."""
    models = _models_root(tmp_path)
    busy = _write_model(models / "PP-OCRv6_medium_rec")
    (busy / "inference.yml").unlink()
    downloads = busy / ".cache" / "huggingface" / "download"
    downloads.mkdir(parents=True)
    leftover = downloads / "inference.yml.incomplete"
    leftover.write_text("partial", encoding="utf-8")
    _make_idle(tmp_path)
    os.utime(leftover, None)  # the download is still writing this file right now

    repair = paddle.repair_incomplete_paddle_model_cache(tmp_path)

    assert repair.removed == ()
    assert busy.is_dir()
    assert "正在下载" in repair.skipped[0][1]


def test_stale_download_leftover_does_not_block_repair(
    tmp_path: Path, pinned_registry: frozenset[str]
) -> None:
    """After a crash the leftover stays behind; it must not disable the repair."""
    models = _models_root(tmp_path)
    crashed = _write_model(models / "PP-OCRv6_medium_rec")
    (crashed / "inference.yml").unlink()
    downloads = crashed / ".cache" / "huggingface" / "download"
    downloads.mkdir(parents=True)
    (downloads / "inference.yml.incomplete").write_text("partial", encoding="utf-8")
    (downloads / "inference.yml.lock").write_text("", encoding="utf-8")
    _make_idle(tmp_path)

    repair = paddle.repair_incomplete_paddle_model_cache(tmp_path)

    assert repair.removed == (crashed,)
    assert not crashed.exists()


def test_cache_held_by_a_download_lock_is_left_alone(
    tmp_path: Path, pinned_registry: frozenset[str]
) -> None:
    """PaddleX holds a cross-process lock for a whole download; respect it."""
    filelock = pytest.importorskip("filelock")
    models = _models_root(tmp_path)
    downloading = _write_model(models / "PP-OCRv6_medium_rec")
    (downloading / "inference.yml").unlink()
    _make_idle(tmp_path)

    lock_path = paddle.official_model_lock_path(tmp_path, downloading.name)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = filelock.FileLock(lock_path)
    failures: list[BaseException] = []
    #: ``FileLock.is_locked`` counts re-entrant acquisitions per thread, so the
    #: competing download announces itself instead of the test polling for it.
    holding = threading.Event()

    def hold() -> None:
        try:
            with holder:
                holding.set()
                time.sleep(1.5)
        except OSError as error:  # pragma: no cover - reported below
            failures.append(error)

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert holding.wait(timeout=5), "the competing download never took the lock"

        repair = paddle.repair_incomplete_paddle_model_cache(tmp_path)
        assert repair.removed == ()
        assert downloading.is_dir()
        assert "下载锁被占用" in repair.skipped[0][1]
    finally:
        thread.join(timeout=5)
    assert failures == []

    repaired = paddle.repair_incomplete_paddle_model_cache(tmp_path)
    assert repaired.removed == (downloading,)
    assert not downloading.exists()


def test_cache_that_became_complete_while_waiting_is_kept(
    tmp_path: Path, pinned_registry: frozenset[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent download that finishes during the repair must not be deleted."""
    models = _models_root(tmp_path)
    racing = _write_model(models / "PP-OCRv6_medium_rec")
    _make_idle(tmp_path)

    answers = iter(["inference.yml 缺失", None])
    real_defect = paddle._model_cache_defect

    def flaky_defect(model_dir: Path) -> str | None:
        answer = next(answers, None)
        return answer if answer is not None else real_defect(model_dir)

    monkeypatch.setattr(paddle, "_model_cache_defect", flaky_defect)

    repair = paddle.repair_incomplete_paddle_model_cache(tmp_path)

    assert repair.removed == ()
    assert racing.is_dir()
    assert (racing / "inference.yml").is_file()


def test_repaired_model_is_kept_on_the_next_pass(
    tmp_path: Path, pinned_registry: frozenset[str]
) -> None:
    """Repair must converge: a freshly downloaded model is never deleted again."""
    models = _models_root(tmp_path)
    stale = _write_model(models / "PP-OCRv6_medium_rec")
    (stale / "inference.pdiparams").write_bytes(b"")
    _make_idle(tmp_path)
    assert paddle.repair_incomplete_paddle_model_cache(tmp_path).removed == (stale,)

    downloaded = _write_model(models / "PP-OCRv6_medium_rec")
    _make_idle(tmp_path)
    second = paddle.repair_incomplete_paddle_model_cache(tmp_path)

    assert second.removed == ()
    assert downloaded.is_dir()


def test_link_into_another_directory_is_never_removed(
    tmp_path: Path, pinned_registry: frozenset[str]
) -> None:
    """Deleting through a link would delete somebody else's files."""
    target = tmp_path / "elsewhere"
    _write_model(target)
    (target / "inference.yml").unlink()
    models = _models_root(tmp_path)
    link = models / "PP-OCRv6_medium_rec"
    if os.name == "nt":
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if created.returncode != 0:
            pytest.skip("this environment does not allow creating a junction")
    else:
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError:
            pytest.skip("this environment does not allow creating a symlink")
    _make_idle(tmp_path)

    repair = paddle.repair_incomplete_paddle_model_cache(tmp_path)

    assert repair.removed == ()
    assert "链接" in repair.skipped[0][1]
    assert (target / "inference.json").is_file()
    assert (target / "inference.pdiparams").is_file()


def test_download_lock_path_matches_paddlex_lock_key(tmp_path: Path) -> None:
    """The repair must take exactly the lock PaddleX takes for the same model.

    Mirrors ``_official_model_download_lock_path``: a SHA-256 over the model
    name, below ``<cache>/locks/official_models``.
    """
    name = "PP-OCRv6_medium_rec"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()

    assert paddle.official_model_lock_path(tmp_path, name) == (
        tmp_path / "locks" / "official_models" / f"{digest}.lock"
    )


def test_repair_keeps_the_scratch_space_inside_the_cache_it_was_given(
    tmp_path: Path, pinned_registry: frozenset[str]
) -> None:
    """Nothing the repair does may escape the cache directory it was handed."""
    models = _models_root(tmp_path)
    broken = _write_model(models / "PP-OCRv6_medium_rec")
    (broken / "inference.yml").unlink()
    _make_idle(tmp_path)

    paddle.repair_incomplete_paddle_model_cache(tmp_path)

    assert sorted(path.name for path in tmp_path.iterdir()) == ["locks", "official_models"]


def test_engine_repairs_incomplete_cache_before_constructing_paddle(
    tmp_path: Path, pinned_registry: frozenset[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    from types import SimpleNamespace

    incomplete = _write_model(tmp_path / "official_models" / "PP-OCRv6_medium_rec")
    (incomplete / "inference.yml").unlink()
    _make_idle(tmp_path)

    class FakePaddleOCR:
        def __init__(self, **_kwargs) -> None:
            assert not incomplete.exists()

    provider = paddle.PaddleOCRProvider()
    monkeypatch.setattr(provider, "_configure_model_cache", lambda: tmp_path)
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=FakePaddleOCR))

    assert isinstance(provider._get_engine(), FakePaddleOCR)
