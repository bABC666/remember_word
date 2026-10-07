import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/phase29'))
import netem_rich_release as release


def test_dirty_code_refused_before_database_authentication(monkeypatch, tmp_path):
    def refuse(_sha):
        raise ValueError('tracked worktree dirty')
    monkeypatch.setattr(release, 'check_code', refuse)
    monkeypatch.setattr(release, 'guard_database', lambda *_a, **_k: pytest.fail('database reached'))
    with pytest.raises(ValueError, match='dirty'):
        release.authenticated_execute(database=tmp_path / 'missing.db', code_sha='a' * 40,
                                      username='admin', password='wrong', operation=tmp_path)


def test_wrong_password_refuses_before_backup_or_migration(monkeypatch, tmp_path):
    database = tmp_path / 'auth.db'
    with sqlite3.connect(database) as c:
        c.execute('create table user (id,username,role,is_active,password_hash)')
        c.execute("insert into user values (1,'admin','admin',1,'test-hash')")
    monkeypatch.setattr(release, 'check_locked_code', lambda _sha: None)
    monkeypatch.setattr(release, 'guard_database', lambda path, **_k: path)
    monkeypatch.setattr(release, 'verify_user_password', lambda *_a: False)
    monkeypatch.setattr(release, 'backup_database', lambda *_a, **_k: pytest.fail('backup reached'))
    with pytest.raises(ValueError, match='administrator'):
        release.authenticated_execute(database=database, code_sha='a' * 40,
                                      username='admin', password='wrong', operation=tmp_path)


def test_authenticated_isolated_migration_apply_backup_and_local_rollback(monkeypatch):
    import netem_rich_import as rich

    directory = ROOT / 'test-artifacts/netem-rich-release-tests' / uuid.uuid4().hex
    database = directory / 'clone/vocab.db'
    source = ROOT / 'test-artifacts/netem-rich-20261007/baseline/vocab.db'
    rich.clone(source, database)
    monkeypatch.setattr(release, 'check_locked_code', lambda _sha: None)
    monkeypatch.setattr(release, 'verify_user_password', lambda *_a: True)
    before = release.state(database)
    result = release.authenticated_execute(database=database, code_sha='a' * 40,
        username='admin', password='test-only', operation=directory / 'apply')
    assert result['result']['changed'] == 5528
    assert release.state(directory / 'apply/before.db') == before
    assert release.state(database) == (release.REVISION, before[1])
    receipt = Path(result['receipt'])
    rolled_back = release.authenticated_execute(database=database, code_sha='a' * 40,
        username='admin', password='test-only', operation=directory / 'rollback',
        action='rollback', receipt=receipt)
    assert rolled_back['result']['rolled_back'] == 5528
    assert release.state(database) == (release.REVISION, before[1])


def test_untracked_release_executables_cannot_pass_old_head(monkeypatch):
    import subprocess
    monkeypatch.setattr(release, 'check_code', lambda _sha: None)
    def missing_blob(args, **_kwargs):
        assert args[:2] == ['git', 'rev-parse']
        raise subprocess.CalledProcessError(128, args)
    monkeypatch.setattr(release.subprocess, 'check_output', missing_blob)
    with pytest.raises(ValueError, match='missing from locked commit'):
        release.check_locked_code('a' * 40)


def test_local_credential_refuses_outside_artifacts_before_decryption(tmp_path, monkeypatch):
    monkeypatch.setattr(release.subprocess, 'run', lambda *_a, **_k: pytest.fail('decryption reached'))
    with pytest.raises(ValueError, match='test-artifacts'):
        release.local_credential(tmp_path / 'credential.json')


def test_local_credential_decryption_is_captured_not_printed(monkeypatch):
    import json
    import subprocess
    path = ROOT / 'test-artifacts/netem-rich-release-tests' / (uuid.uuid4().hex + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'username': 'admin', 'encrypted_password': 'DPAPI-test-only'}))
    calls = []
    def decrypt(args, **kwargs):
        assert kwargs['capture_output'] is True
        assert kwargs['env']['NETEM_LOCAL_CREDENTIAL'] == str(path.resolve())
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, 'test-only-password', '')
    monkeypatch.setattr(release.subprocess, 'run', decrypt)
    try:
        assert release.local_credential(path) == ('admin', 'test-only-password')
        assert len(calls) == 1
    finally:
        path.unlink()


def test_consumed_local_credential_is_deleted_on_release_failure(monkeypatch):
    path = ROOT / 'test-artifacts/netem-rich-release-tests' / (uuid.uuid4().hex + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('encrypted test-only placeholder')
    monkeypatch.setattr(release, 'local_credential', lambda _path: ('admin', 'test-only'))
    with pytest.raises(ValueError, match='authentication failure'), release.consume_local_credential(path) as credential:
        assert credential == ('admin', 'test-only')
        raise ValueError('authentication failure')
    assert not path.exists()
