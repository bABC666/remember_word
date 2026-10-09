"""Release repair must be atomic and fail closed; fixtures contain no real data."""

import importlib.util
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from tests.conftest import run_alembic

SPEC = importlib.util.spec_from_file_location(
    "repair_session_id_0015", Path(__file__).resolve().parents[2] / "tools/repair_session_id_0015.py"
)
assert SPEC and SPEC.loader
repair_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair_module)


@pytest.fixture
def evidence(tmp_path):
    target, backup, historical, baseline = [tmp_path / name for name in
                                           ("target.db", "backup.db", "old.db", "baseline.json")]
    with sqlite3.connect(target) as db:
        db.executescript("""
            CREATE TABLE alembic_version(version_num TEXT);
            INSERT INTO alembic_version VALUES('0014_selected_lexicon');
            CREATE TABLE user(id INTEGER PRIMARY KEY, username TEXT);
            INSERT INTO user VALUES(2, 'release_smoke_20261003');
            CREATE TABLE user_session(id INTEGER PRIMARY KEY, user_id INTEGER, token_hash TEXT,
              created_at TEXT, expires_at TEXT, last_seen_at TEXT, revoked_at TEXT, user_agent TEXT);
            INSERT INTO user_session VALUES(1,2,'synthetic-new','2026-10-03','2027-01-01',NULL,NULL,'test');
            CREATE UNIQUE INDEX ix_token ON user_session(token_hash);
            CREATE TABLE history_event(id INTEGER PRIMARY KEY, user_id INTEGER, event_type TEXT,
              timestamp TEXT, entity_type TEXT, entity_id INTEGER);
            INSERT INTO history_event VALUES(1,2,'user_login','2026-10-03',NULL,NULL);
        """)
    shutil.copyfile(target, historical)
    with sqlite3.connect(historical) as db:
        db.execute("UPDATE alembic_version SET version_num='0007_bridge_foreign_keys'")
        db.execute("UPDATE user_session SET user_id=1,token_hash='synthetic-old',created_at='2026-09-01'")
    shutil.copyfile(target, backup)
    baseline.write_text(json.dumps({"alembic_revision": "0007_bridge_foreign_keys",
                                    "sha256": repair_module.file_hash(historical)}))
    return {"database": target, "backup": backup, "historical": historical, "baseline": baseline,
            "backup_sha256": repair_module.file_hash(backup)}


def test_apply_preserves_non_id_fields_and_dry_run_rolls_back(evidence):
    before = evidence["database"].read_bytes()
    assert repair_module.repair(**evidence)["committed"] is False
    assert evidence["database"].read_bytes() == before
    result = repair_module.repair(**evidence, apply=True)
    assert result["non_id_fields_unchanged"] == 7
    with sqlite3.connect(evidence["database"]) as db:
        assert db.execute("SELECT id,user_id FROM user_session").fetchall() == [(2, 2)]
    with pytest.raises(ValueError, match="differs"):
        repair_module.repair(**evidence, apply=True)


@pytest.mark.parametrize("sql", [
    "UPDATE alembic_version SET version_num='0015_session_autoincrement'",
    "UPDATE user_session SET user_id=1",
    "UPDATE user_session SET token_hash='synthetic-old'",
    "UPDATE user_session SET created_at='2026-01-01'",
    "DELETE FROM history_event",
    "UPDATE history_event SET entity_type='user_session',entity_id=1",
    "UPDATE user_session SET id=2",
    "CREATE TABLE reference(id INTEGER REFERENCES user_session(id))",
    "CREATE TRIGGER surprise AFTER UPDATE ON user_session BEGIN DELETE FROM user; END",
    "UPDATE user SET username='unexpected'",
])
def test_each_collision_precondition_refuses_without_writes(evidence, sql):
    with sqlite3.connect(evidence["database"]) as db:
        db.execute(sql)
    shutil.copyfile(evidence["database"], evidence["backup"])
    evidence["backup_sha256"] = repair_module.file_hash(evidence["backup"])
    before = evidence["database"].read_bytes()
    with pytest.raises(ValueError):
        repair_module.repair(**evidence, apply=True)
    assert evidence["database"].read_bytes() == before


def test_target_drift_and_evidence_tampering_are_rejected(evidence):
    with sqlite3.connect(evidence["database"]) as db:
        db.execute("UPDATE user_session SET last_seen_at='2026-10-04'")
    with pytest.raises(ValueError, match="differs"):
        repair_module.repair(**evidence, apply=True)
    evidence["backup_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="backup hash"):
        repair_module.repair(**evidence, apply=True)


def test_historical_evidence_predicates_are_checked_in_transaction(evidence):
    with sqlite3.connect(evidence["historical"]) as db:
        db.execute("UPDATE user_session SET user_id=2")
    evidence["baseline"].write_text(json.dumps({"alembic_revision": "0007_bridge_foreign_keys",
                                              "sha256": repair_module.file_hash(evidence["historical"])}))
    before = evidence["database"].read_bytes()
    with pytest.raises(ValueError, match="historical session set"):
        repair_module.repair(**evidence, apply=True)
    assert evidence["database"].read_bytes() == before


@pytest.fixture
def real_chain_evidence(tmp_path):
    """Both source revisions are built by their actual Alembic chains."""
    target = tmp_path / "real-0014.db"
    historical = tmp_path / "real-0007.db"
    backup = tmp_path / "real-backup.db"
    baseline = tmp_path / "real-baseline.json"
    for database, revision in ((target, "0014_selected_lexicon"),
                               (historical, "0007_bridge_foreign_keys")):
        upgrade = run_alembic(database, "upgrade", revision)
        assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
        with sqlite3.connect(database) as db:
            db.execute("INSERT INTO user (id,username,display_name,password_hash,role,is_active,"
                       "created_at,updated_at) VALUES (2,'release_smoke_20261003','smoke','!',"
                       "'user',1,'2026-09-01','2026-09-01')")
            db.execute("INSERT INTO user_session (id,user_id,token_hash,created_at,expires_at,"
                       "user_agent) VALUES (1,?,?,?,?,?)",
                       (2 if database == target else 1,
                        "synthetic-new" if database == target else "synthetic-old",
                        "2026-10-03" if database == target else "2026-09-01",
                        "2027-01-01", "test"))
            if database == target:
                db.execute("INSERT INTO history_event (user_id,event_type,timestamp,"
                           "entity_type,entity_id,payload) VALUES "
                           "(2,'user_login','2026-10-03','user',2,'{}')")
    shutil.copyfile(target, backup)
    baseline.write_text(json.dumps({"alembic_revision": "0007_bridge_foreign_keys",
                                    "sha256": repair_module.file_hash(historical)}))
    return {"database": target, "backup": backup, "historical": historical,
            "baseline": baseline, "backup_sha256": repair_module.file_hash(backup)}


def test_real_0014_chain_keeps_five_unrelated_triggers(real_chain_evidence):
    with sqlite3.connect(real_chain_evidence["database"]) as db:
        triggers = db.execute("SELECT name, tbl_name FROM sqlite_master "
                              "WHERE type='trigger' ORDER BY name").fetchall()
    assert len(triggers) == 5
    assert all(table != "user_session" for _, table in triggers)
    result = repair_module.repair(**real_chain_evidence, apply=True)
    assert result["passed"] and result["non_id_fields_unchanged"] == 7
    with sqlite3.connect(real_chain_evidence["database"]) as db:
        assert db.execute("SELECT id,user_id FROM user_session").fetchall() == [(2, 2)]
        assert db.execute("SELECT name,tbl_name FROM sqlite_master WHERE type='trigger' "
                          "ORDER BY name").fetchall() == triggers
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_real_0014_chain_refuses_session_trigger_and_rolls_back(real_chain_evidence):
    target = real_chain_evidence["database"]
    with sqlite3.connect(target) as db:
        db.execute("CREATE TRIGGER dangerous AFTER UPDATE ON user_session "
                   "BEGIN DELETE FROM user WHERE id=2; END")
    shutil.copyfile(target, real_chain_evidence["backup"])
    real_chain_evidence["backup_sha256"] = repair_module.file_hash(real_chain_evidence["backup"])
    before = target.read_bytes()
    with pytest.raises(ValueError, match="unexpected user_session trigger"):
        repair_module.repair(**real_chain_evidence, apply=True)
    assert target.read_bytes() == before
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT id,user_id FROM user_session").fetchall() == [(1, 2)]
        assert db.execute("SELECT username FROM user WHERE id=2").fetchone() == (
            "release_smoke_20261003",)
