"""Exercise the new empty-only entry point on isolated online-backup copies."""

import hashlib
import importlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import User

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "test-artifacts/netem-gap-20261005/candidate"
BEFORE = ROOT / "test-artifacts/netem-gap-20261005/latest-before.db"
EXPECTED = "5fcc8dfa504ab1d008ce9f015ab2848a6c65f75cf64474eb8d56188cb42ae146"


def module():
    path = ROOT / "backend/app/services/public_lexicon_gap.py"
    assert path.is_file(), "empty-only atomic update entry point is missing"
    return importlib.import_module("app.services.public_lexicon_gap")


def rows(database):
    with sqlite3.connect(database) as con:
        names = con.execute("select name from sqlite_master where type='table' order by name")
        return {name: hashlib.sha256(repr(con.execute(f'select * from "{name}"').fetchall()).encode()).hexdigest()
                for name, in names.fetchall()}


@pytest.fixture
def copy_db(tmp_path):
    if not BEFORE.exists():
        pytest.skip("local latest online-backup receipt unavailable")
    database = tmp_path / "vocab.db"
    shutil.copyfile(BEFORE, database)
    return database


def apply(database, plan, action="apply"):
    engine = create_engine("sqlite:///" + database.as_posix())
    try:
        with Session(engine) as session:
            admin = session.get(User, 1)
            return module().confirm_gap(session, plan=plan, administrator=admin,
                                        candidate=PACKAGE, expected_sha=EXPECTED, action=action)
    finally:
        engine.dispose()


def test_atomic_empty_update_repeat_and_local_retraction_preserve_new_user_data(copy_db):
    gap = module()
    plan = gap.build_gap_plan(copy_db, candidate=PACKAGE, expected_sha=EXPECTED)
    before = rows(copy_db)
    result = apply(copy_db, plan)
    assert result["status"] == "applied" and result["entries_updated"] == 77
    first = rows(copy_db)
    assert apply(copy_db, plan)["status"] == "already_applied"
    assert rows(copy_db) == first
    with sqlite3.connect(copy_db) as con:
        con.execute("insert into app_setting(key,value,updated_at) values('gap_new_user_data','keep','2026-10-05')")
    user_data = rows(copy_db)["app_setting"]
    assert apply(copy_db, plan, "retract")["status"] == "retracted"
    reverted = rows(copy_db)
    assert reverted["lexicon_entry"] == before["lexicon_entry"]
    assert reverted["app_setting"] == user_data
    assert reverted["user_word_state"] == before["user_word_state"]
    assert reverted["review_event"] == before["review_event"]
    assert apply(copy_db, plan, "retract")["status"] == "already_retracted"
    assert rows(copy_db) == reverted
    with pytest.raises(gap.GapRefused, match="retracted"):
        apply(copy_db, plan)
    engine = create_engine("sqlite:///" + copy_db.as_posix())
    from app.services.entry_provenance import entry_sources, selected_meaning_sources
    with Session(engine) as session:
        ids = [r["entry_id"] for r in plan["changes"]]
        assert selected_meaning_sources(session, ids) == {}
        fields = entry_sources(session, ids[0])["fields"]
        meaning = next(f for f in fields if f["field_kind"] == "meaning")
        assert meaning["selected"] == []
        assert meaning["candidates"][0]["selection_retracted"] is True
    engine.dispose()


@pytest.mark.parametrize("mutation", ["meaning", "word", "sequence", "target", "first_run"])
def test_changed_target_or_nonempty_value_refuses_every_write(copy_db, mutation):
    gap = module()
    plan = gap.build_gap_plan(copy_db, candidate=PACKAGE, expected_sha=EXPECTED)
    with sqlite3.connect(copy_db) as con:
        target = plan["changes"][-1]["entry_id"]
        sql = {"meaning": "update lexicon_entry set source_meanings='[\"newer\"]' where id=?",
               "word": "update lexicon_entry set word='identity changed' where id=?",
               "sequence": "update lexicon_entry set sequence=9000 where id=?",
               "target": "update lexicon set visibility='private' where id=?",
               "first_run": "update public_import_run set plan_sha256='different' where id=?"}[mutation]
        con.execute(sql, (5 if mutation == "target" else 1 if mutation == "first_run" else target,))
    before = rows(copy_db)
    with pytest.raises(gap.GapRefused):
        apply(copy_db, plan)
    assert rows(copy_db) == before


def test_exception_after_twentieth_update_rolls_back_content_sources_and_audit(copy_db, monkeypatch):
    gap = module()
    plan = gap.build_gap_plan(copy_db, candidate=PACKAGE, expected_sha=EXPECTED)
    before = rows(copy_db)
    real = gap._write_meaning
    count = 0

    def fail(*args, **kwargs):
        nonlocal count
        real(*args, **kwargs)
        count += 1
        if count == 20:
            raise RuntimeError("intentional twentieth write failure")

    monkeypatch.setattr(gap, "_write_meaning", fail)
    with pytest.raises(RuntimeError, match="twentieth"):
        apply(copy_db, plan)
    assert count == 20 and rows(copy_db) == before


def test_modified_plan_and_recomputed_digest_cannot_invent_meaning(copy_db):
    gap = module()
    plan = gap.build_gap_plan(copy_db, candidate=PACKAGE, expected_sha=EXPECTED)
    plan["changes"][0]["new_meanings"] = ["invented"]
    plan["plan_sha256"] = gap.plan_digest(plan)
    before = rows(copy_db)
    with pytest.raises(gap.GapRefused):
        apply(copy_db, plan)
    assert rows(copy_db) == before


def test_candidate_self_rehash_is_not_an_authority(copy_db, tmp_path):
    gap = module()
    candidate = tmp_path / "candidate"
    shutil.copytree(PACKAGE, candidate)
    ledger = json.loads((candidate / "ledger.json").read_text("utf8"))
    ledger[0]["new_meanings"] = ["invented"]
    (candidate / "ledger.json").write_text(json.dumps(ledger), encoding="utf8")
    with pytest.raises(gap.GapRefused):
        gap.build_gap_plan(copy_db, candidate=candidate, expected_sha=EXPECTED)


def test_forged_original_timestamp_refuses_before_writes(copy_db):
    gap = module()
    plan = gap.build_gap_plan(copy_db, candidate=PACKAGE, expected_sha=EXPECTED)
    plan["changes"][0]["old_updated_at"] = "invented timestamp"
    plan["plan_sha256"] = gap.plan_digest(plan)
    before = rows(copy_db)
    with pytest.raises(gap.GapRefused, match="original columns"):
        apply(copy_db, plan)
    assert rows(copy_db) == before


@pytest.mark.parametrize("profile", ["inactive", "ordinary"])
def test_administrator_status_is_checked_inside_locked_transaction(copy_db, profile):
    gap = module()
    plan = gap.build_gap_plan(copy_db, candidate=PACKAGE, expected_sha=EXPECTED)
    engine = create_engine("sqlite:///" + copy_db.as_posix())
    with Session(engine) as session:
        admin = session.get(User, 1)
        with sqlite3.connect(copy_db) as con:
            con.execute("update user set is_active=0 where id=1" if profile == "inactive"
                        else "update user set role='user' where id=1")
        before = rows(copy_db)
        with pytest.raises(gap.GapRefused):
            gap.confirm_gap(session, plan=plan, administrator=admin, candidate=PACKAGE,
                            expected_sha=EXPECTED)
        assert rows(copy_db) == before
    engine.dispose()
