"""Catch unsafe paths, hidden inputs and accidental reseeding of learner data."""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
from pathlib import Path

import pytest


def runtime():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    return importlib.import_module("classmate_runtime")


def test_classmate_runtime_is_available():
    import importlib.util
    assert importlib.util.find_spec("app.classmate_dataset") is not None


def test_inherited_config_cannot_redirect_classmate_data(tmp_path, monkeypatch):
    monkeypatch.setenv("VOCAB_DATABASE_PATH", str(tmp_path / "secret.db"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    env = runtime().isolated_env(tmp_path)
    assert env["VOCAB_DATABASE_PATH"] == str(tmp_path / "data/classmate-test/classmate.sqlite3")
    assert env["VOCAB_ENABLE_OCR"] == "false"
    assert "DEEPSEEK_API_KEY" not in env


def test_reset_keeps_sibling_and_rejects_link(tmp_path):
    rt = runtime()
    target = tmp_path / "data/classmate-test"
    target.mkdir(parents=True)
    (target / "private.txt").write_text("synthetic")
    sibling = tmp_path / "data/protected.txt"
    sibling.write_text("retain")
    rt.reset_data(tmp_path)
    assert not target.exists()
    assert sibling.read_text() == "retain"
    target.mkdir()
    os.link(sibling, target / "linked.txt")
    with pytest.raises(ValueError, match="link"):
        rt.reset_data(tmp_path)
    assert sibling.read_text() == "retain"


def test_linked_data_directory_is_refused(tmp_path):
    import subprocess
    rt = runtime()
    outside = tmp_path / "outside"
    outside.mkdir()
    data = tmp_path / "data"
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(data), str(outside)],
                                capture_output=True, check=False)
        assert result.returncode == 0
    else:
        data.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="link"):
            rt.guarded_paths(tmp_path)
    finally:
        data.rmdir() if os.name == "nt" else data.unlink()


def test_stop_does_not_terminate_an_unrelated_reused_pid(tmp_path):
    import subprocess
    import sys
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(40)"])
    try:
        target = tmp_path / "data/classmate-test"
        target.mkdir(parents=True)
        (target / "server.json").write_text(json.dumps({"pid": child.pid,
                                                       "instance": "unrelated"}))
        runtime().stop(tmp_path)
        assert child.poll() is None
        assert not (target / "server.json").exists()
    finally:
        child.terminate()
        child.wait(timeout=10)


def test_setup_retains_existing_synthetic_learner_data(tmp_path):
    from app.classmate_dataset import BUNDLE_SHA256
    target = tmp_path / "data/classmate-test"
    target.mkdir(parents=True)
    db = target / "classmate.sqlite3"
    db.write_bytes(b"retained synthetic learner data")
    (target / "dataset.json").write_text(json.dumps({"package_sha256": BUNDLE_SHA256}))
    # The installed package is immutable; a directory link is unnecessary.
    import shutil
    source = Path(__file__).resolve().parents[2] / "assets/classmate-test"
    shutil.copytree(source, tmp_path / "assets/classmate-test")
    result = runtime().setup(tmp_path)
    assert result["status"] == "retained"
    assert db.read_bytes() == b"retained synthetic learner data"


def test_public_bundle_is_verified_before_database_creation(tmp_path):
    from app.classmate_dataset import build_database, verify_bundle
    bundle = Path(__file__).resolve().parents[2] / "assets/classmate-test"
    assert verify_bundle(bundle)
    # A fake self-declared package cannot authorize itself.
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "fingerprints.json").write_text(json.dumps({"package_sha256": "0" * 64,
                                                       "files": {}}))
    with pytest.raises(ValueError):
        build_database(tmp_path / "data/classmate-test/classmate.sqlite3", fake)
    assert not (tmp_path / "data").exists()


def test_new_migration_database_has_only_public_content_and_synthetic_accounts(tmp_path):
    from app.classmate_dataset import build_database
    from app.security import verify_password
    bundle = Path(__file__).resolve().parents[2] / "assets/classmate-test"
    database = tmp_path / "data/classmate-test/classmate.sqlite3"
    result = build_database(database, bundle)
    assert result["words"] == 5528
    assert result["empty"] == 0
    assert result["evidence"] == 16507
    with sqlite3.connect(database) as db:
        users = db.execute('select username,password_hash,role,is_active from user').fetchall()
        assert len(users) == 2
        admin, learner = users
        assert admin == ("admin", "!", "admin", 0)
        assert learner[0] == "classmate_test" and learner[2:] == ("user", 1)
        assert verify_password("Shici-Test-2026!", learner[1])
        for table in ["user_session", "history_event", "user_word_state", "review_event",
                      "import_batch", "article"]:
            assert db.execute(f'select count(*) from "{table}"').fetchone()[0] == 0
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert db.execute("pragma foreign_key_check").fetchall() == []
        assert db.execute("select count(*) from lexicon where owner_user_id is not null").fetchone()[0] == 0
    # An existing database is never cleaned, replaced or silently seeded again.
    original = database.read_bytes()
    with pytest.raises(ValueError, match="existing"):
        build_database(database, bundle)
    assert database.read_bytes() == original
