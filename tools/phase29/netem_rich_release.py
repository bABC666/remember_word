"""Protected NETEM extraction release: same clean-code/path/admin gates as prior release.

No public flag on the isolated importer unlocks production. This operator entry
backs up immediately before the explicit additive migration and batch update.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import netem_rich_import as rich
from netem_release import ROOT, check_code, guard_database, readonly

from app.models import User
from app.services.auth import verify_user_password

PLAN = ROOT / 'test-artifacts/netem-rich-20261007/full-release-preview/plan.json'
PLAN_SHA = 'c71f1b1238da8f5c896bee6e91e36a399c79dbccc985d4d7bf8115c6335ba696'
OLD_REVISION = '0015_session_autoincrement'
REVISION = '0016_dictionary_extraction'


def check_locked_code(code_sha):
    check_code(code_sha)
    # Prior gate ignores untracked files; require every executable release file
    # (including this new entry point and revision) to belong to the fixed commit.
    files = {Path(__file__).resolve(), Path(rich.__file__).resolve(),
             ROOT / 'tools/phase29/netem_release.py',
             ROOT / 'tools/phase29/netem_rich_credential.ps1',
             ROOT / 'tools/phase29/netem-rich-pilot-review.json'}
    for directory, patterns in [(ROOT / 'backend/app', ['*.py']),
                                (ROOT / 'backend/alembic', ['*.py']),
                                (ROOT / 'frontend/src', ['*.ts', '*.tsx', '*.css'])]:
        for pattern in patterns:
            files.update(p for p in directory.rglob(pattern) if '.test.' not in p.name)
    for path in sorted(files):
        relative = path.relative_to(ROOT).as_posix()
        try:
            expected = subprocess.check_output(['git', 'rev-parse', f'{code_sha}:{relative}'],
                                               cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        except subprocess.CalledProcessError as error:
            raise ValueError(f'release executable missing from locked commit: {relative}') from error
        actual = subprocess.check_output(['git', 'hash-object', f'--path={relative}', str(path)],
                                         cwd=ROOT, text=True).strip()
        if actual != expected:
            raise ValueError(f'release executable differs from locked commit: {relative}')


def write_new(path, value):
    with path.open('x', encoding='utf-8') as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def authenticate(database, username, password):
    with readonly(database) as c:
        row = c.execute('select id,username,role,is_active,password_hash from user where username=?',
                        (username,)).fetchone()
    if not row or row[2] != 'admin' or not row[3]:
        raise ValueError('active local administrator required; no writes')
    user = User(id=row[0], username=row[1], role=row[2], is_active=bool(row[3]), password_hash=row[4])
    if not verify_user_password(user, password):
        raise ValueError('administrator password verification failed; no writes')
    return row


def local_credential(path):
    """Read a CurrentUser DPAPI credential; plaintext never enters tool output."""
    path = path.resolve()
    if not path.is_relative_to((ROOT / 'test-artifacts').resolve()) or not path.is_file():
        raise ValueError('existing local credential in test-artifacts required')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not data.get('username') or not data.get('encrypted_password'):
        raise ValueError('invalid local credential')
    script = '''$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$credential = Get-Content -LiteralPath $env:NETEM_LOCAL_CREDENTIAL -Raw -Encoding UTF8 | ConvertFrom-Json
$secret = ConvertTo-SecureString -String $credential.encrypted_password
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try { [Console]::Write([Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
'''
    env = dict(os.environ, NETEM_LOCAL_CREDENTIAL=str(path))
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            env=env, capture_output=True, text=True, encoding='utf-8', check=False)
    if result.returncode or not result.stdout:
        raise ValueError('local DPAPI decryption failed; no writes')
    return data['username'], result.stdout


@contextmanager
def consume_local_credential(path):
    path = path.resolve()
    if not path.is_relative_to((ROOT / 'test-artifacts').resolve()) or not path.is_file():
        raise ValueError('existing local credential in test-artifacts required')
    try:
        yield local_credential(path)
    finally:
        path.unlink(missing_ok=True)


def verify_plan(database):
    if rich.sha(PLAN.read_bytes()) != PLAN_SHA:
        raise ValueError('approved fixed plan checksum mismatch')
    plan = json.loads(PLAN.read_bytes())
    for name, meta in plan['source_files'].items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT) or rich.sha(path.read_bytes()) != meta['sha256']:
            raise ValueError('fixed source checksum mismatch')
    with rich.read_only(database) as c:
        for record in plan['records']:
            row = c.execute('select * from lexicon_entry where id=?', (record['entry_id'],)).fetchone()
            if (row is None or row['lexicon_id'] != plan['lexicon_id']
                    or rich.digest(rich.original_baseline(row)) != record['baseline_sha256']
                    or rich.digest(record['payload']) != record['payload_sha256']):
                raise ValueError('approved plan baseline drift')


def state(database):
    with rich.read_only(database) as c:
        revision = c.execute('select version_num from alembic_version').fetchall()
        if len(revision) != 1:
            raise ValueError('ambiguous database revision')
        if c.execute('pragma integrity_check').fetchone()[0] != 'ok' or c.execute('pragma foreign_key_check').fetchall():
            raise ValueError('database integrity/FK gate failed')
        return revision[0][0], rich.table_fingerprints(c)


def backup_database(database, operation):
    backup = operation / 'before.db'
    if backup.exists():
        raise ValueError('refusing to overwrite publication-time backup')
    before = state(database)
    with readonly(database) as src, sqlite3.connect(backup) as dst:
        src.backup(dst)
    if state(backup) != before:
        raise ValueError('backup verification failed')
    write_new(operation / 'backup.json', {'created_at': datetime.now(UTC).isoformat(),
              'database': str(database.resolve()), 'backup': str(backup),
              'sha256': rich.sha(backup.read_bytes()), 'revision': before[0],
              'preserved_tables': before[1], 'integrity_check': 'ok', 'foreign_key_errors': 0})
    return before


def authenticated_execute(*, database, code_sha, username, password, operation,
                          production=False, action='apply', receipt=None):
    # Fail closed, in the same order as the existing protected NETEM release.
    check_locked_code(code_sha)
    database = guard_database(database, production=production)
    identity = authenticate(database, username, password)
    if action not in {'apply', 'rollback'}:
        raise ValueError('unknown release action')
    operation = operation.resolve()
    if not operation.is_relative_to((ROOT / 'test-artifacts').resolve()) or operation.exists():
        raise ValueError('new operation directory inside test-artifacts required')
    if production:
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(('127.0.0.1', 8000)) == 0:
                raise ValueError('maintenance required: stop formal service using scripts/stop-vocab.ps1')
    if action == 'apply':
        verify_plan(database)
    else:
        if receipt is None or not receipt.resolve().is_relative_to((ROOT / 'test-artifacts').resolve()):
            raise ValueError('explicit local batch receipt required')
        undo = json.loads(receipt.read_bytes())
        if undo['plan_sha256'] != PLAN_SHA or Path(undo['database']).resolve() != database:
            raise ValueError('receipt belongs to another database/plan')
    revision, _ = state(database)
    if revision not in {OLD_REVISION, REVISION} or (action == 'rollback' and revision != REVISION):
        raise ValueError('unsupported migration baseline')
    operation.mkdir(parents=True)
    before = backup_database(database, operation)
    if authenticate(database, username, password) != identity:
        raise ValueError('administrator changed before migration')
    check_locked_code(code_sha)
    if action == 'apply' and revision == OLD_REVISION:
        subprocess.run([sys.executable, '-m', 'alembic', '-x',
                        'db_url=sqlite:///' + database.as_posix(), 'upgrade', REVISION],
                       cwd=ROOT / 'backend', check=True)
    if state(database) != (REVISION, before[1]):
        raise ValueError('migration preservation gate failed; no batch applied')

    def authorized_connection(path):
        check_locked_code(code_sha)
        if guard_database(path, production=production) != database:
            raise ValueError('database identity changed')
        c = sqlite3.connect(database)
        c.row_factory = sqlite3.Row
        c.execute('pragma foreign_keys=on')
        return c

    def verify_locked_identity(c):
        current = c.execute('select id,username,role,is_active,password_hash from user where id=?',
                            (identity[0],)).fetchone()
        if current is None or tuple(current) != identity:
            raise ValueError('administrator changed before batch')

    if action == 'apply':
        receipt = operation / 'batch-undo.json'
        result = rich.apply(database, PLAN, PLAN_SHA, receipt, _connect=authorized_connection,
                            _verify=verify_locked_identity)
    else:
        result = rich.rollback(database, receipt, _connect=authorized_connection,
                               _verify=verify_locked_identity)
    after = state(database)
    if after != (REVISION, before[1]):
        raise ValueError('post-release preservation gate failed')
    report = {'action': action, 'time': datetime.now(UTC).isoformat(),
              'code_sha': code_sha, 'plan_sha256': PLAN_SHA, 'administrator_id': identity[0],
              'database': str(database), 'revision': REVISION, 'result': result,
              'receipt': str(receipt.resolve()), 'preserved_tables_equal': True,
              'preserved_tables': after[1], 'confirmed_core_meanings_added': 0,
              'integrity_check': 'ok', 'foreign_key_errors': 0}
    write_new(operation / 'result.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['apply', 'rollback'])
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--operation', required=True, type=Path)
    parser.add_argument('--code-sha', required=True)
    parser.add_argument('--confirm', required=True)
    parser.add_argument('--admin', default='admin')
    parser.add_argument('--production-update', action='store_true')
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--credential-file', type=Path)
    args = parser.parse_args()
    if args.confirm != PLAN_SHA:
        raise ValueError('exact approved plan digest required')
    check_locked_code(args.code_sha)
    if args.credential_file:
        with consume_local_credential(args.credential_file) as (username, password):
            if username != args.admin:
                raise ValueError('local credential administrator mismatch')
            report = authenticated_execute(database=args.database, code_sha=args.code_sha,
                username=args.admin, password=password, operation=args.operation,
                production=args.production_update, action=args.action, receipt=args.receipt)
    else:
        password = getpass.getpass('管理员当前口令（仅本机）：')
        report = authenticated_execute(database=args.database, code_sha=args.code_sha,
            username=args.admin, password=password, operation=args.operation,
            production=args.production_update, action=args.action, receipt=args.receipt)
    print(json.dumps({k: v for k, v in report.items() if k != 'preserved_tables'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
