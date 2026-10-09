import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/phase29'))
import netem_source_audit_release as release


def test_release_refuses_unlocked_code_before_database(monkeypatch, tmp_path):
    def refuse(_sha):
        raise ValueError('unlocked code')
    monkeypatch.setattr(release, 'check_code', refuse)
    monkeypatch.setattr(release.protected, 'guard_database', lambda *_a, **_k: pytest.fail('database reached'))
    with pytest.raises(ValueError, match='unlocked'):
        release.authenticated_execute(database=tmp_path/'missing.db', code_sha='a'*40,
            username='admin', password='wrong', operation=tmp_path)


def test_release_requires_real_administrator_before_backup(monkeypatch, tmp_path):
    database = tmp_path/'auth.db'
    with sqlite3.connect(database) as c:
        c.execute('create table user(id,username,role,is_active,password_hash)')
        c.execute("insert into user values(1,'ordinary','user',1,'test-only')")
    monkeypatch.setattr(release, 'check_code', lambda _sha: None)
    monkeypatch.setattr(release.protected, 'guard_database', lambda p, **_k: p)
    monkeypatch.setattr(release.protected, 'backup_database', lambda *_a, **_k: pytest.fail('backup reached'))
    with pytest.raises(ValueError, match='administrator'):
        release.authenticated_execute(database=database, code_sha='a'*40,
            username='ordinary', password='unused', operation=tmp_path)


def test_frozen_audit_authenticated_apply_and_rollback_restore_existing_source_rows(monkeypatch, netem_production_sentinel):
    from netem_release import engine_for
    from sqlalchemy.orm import Session

    from app.cli import set_password_for_user

    directory = ROOT/'test-artifacts/netem-source-audit-release-tests'/uuid.uuid4().hex
    database = directory/'clone/vocab.db'
    release.protected.rich.clone(ROOT/'test-artifacts/netem-source-audit-20261008/full-db/vocab.db', database)
    engine = engine_for(database)
    try:
        with Session(engine) as s:
            assert set_password_for_user(s, 'admin', 'source-audit-test-only-password')
    finally:
        engine.dispose()
    monkeypatch.setattr(release, 'check_code', lambda _sha: None)
    before = release.protected.state(database)
    with release.protected.rich.read_only(database) as c:
        old = {r['lexicon_entry_id']:r['payload_sha256'] for r in c.execute('select * from entry_dictionary_extraction')}
    with pytest.raises(ValueError, match='password verification'):
        release.authenticated_execute(database=database, code_sha='a'*40, username='admin',
            password='wrong', operation=directory/'bad-auth')
    assert not (directory/'bad-auth').exists()
    result = release.authenticated_execute(database=database, code_sha='a'*40, username='admin',
        password='source-audit-test-only-password', operation=directory/'apply')
    assert result['result']['changed'] == 5528
    assert release.protected.state(directory/'apply/before.db') == before
    assert release.protected.state(database) == before
    undone = release.authenticated_execute(database=database, code_sha='a'*40, username='admin',
        password='source-audit-test-only-password', operation=directory/'rollback', action='rollback',
        receipt=Path(result['receipt']))
    assert undone['result']['rolled_back'] == 5528
    with release.protected.rich.read_only(database) as c:
        assert old == {r['lexicon_entry_id']:r['payload_sha256'] for r in c.execute('select * from entry_dictionary_extraction')}
    assert release.protected.state(database) == before
