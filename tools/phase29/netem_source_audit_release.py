"""Protected release/retraction for the frozen 2026-10-08 source-audit candidate.

Requires a clean real commit, the exact plan digest, active administrator password,
fixed production path, maintenance, publication-time backup and local undo receipt.
The old extraction release remains available for its own original batch only.
"""
from __future__ import annotations

import argparse
import getpass
import json
import socket
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import netem_rich_release as protected
import netem_source_audit as audit

ROOT = protected.ROOT
PLAN = ROOT / 'test-artifacts/netem-source-audit-20261008/full-recovery-locked/plan.json'
PLAN_SHA = '29e90f367ea571773b64aedeb639bd59d01ac1c073088b0de2a4e53a3a11cb10'


def check_code(code_sha):
    protected.check_locked_code(code_sha)
    for path in [Path(__file__).resolve(), Path(audit.__file__).resolve(),
                 ROOT / 'tools/phase29/netem_source_audit_browser.cjs']:
        relative = path.relative_to(ROOT).as_posix()
        try:
            expected = subprocess.check_output(['git', 'rev-parse', f'{code_sha}:{relative}'],
                                               cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        except subprocess.CalledProcessError as error:
            raise ValueError('audit release code is not in the locked commit') from error
        actual = subprocess.check_output(['git', 'hash-object', f'--path={relative}', str(path)], cwd=ROOT, text=True).strip()
        if expected != actual:
            raise ValueError('audit release code differs from locked commit')


def validate_plan(database):
    if protected.rich.sha(PLAN.read_bytes()) != PLAN_SHA:
        raise ValueError('fixed source audit plan checksum mismatch')
    plan = json.loads(PLAN.read_bytes())
    if plan.get('audit_version') != audit.AUDIT_VERSION:
        raise ValueError('source audit version mismatch')
    for name, meta in plan['source_files'].items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT / 'test-artifacts') or protected.rich.sha(path.read_bytes()) != meta['sha256']:
            raise ValueError('fixed source audit evidence changed')
    with protected.rich.read_only(database) as c:
        for record in plan['records']:
            entry = c.execute('select * from lexicon_entry where id=?', (record['entry_id'],)).fetchone()
            row = c.execute('select * from entry_dictionary_extraction where lexicon_entry_id=?', (record['entry_id'],)).fetchone()
            if (entry is None or row is None or entry['lexicon_id'] != plan['lexicon_id']
                    or protected.rich.digest(protected.rich.original_baseline(entry)) != record['baseline_sha256']
                    or row['payload_sha256'] not in {record['baseline_extraction_sha256'], record['payload_sha256']}
                    or protected.rich.digest(json.loads(row['payload'])) != row['payload_sha256']):
                raise ValueError('source/entry baseline drift')
    return plan


def authenticated_execute(*, database, code_sha, username, password, operation,
                          production=False, action='apply', receipt=None):
    check_code(code_sha)
    database = protected.guard_database(database, production=production)
    identity = protected.authenticate(database, username, password)
    operation = operation.resolve()
    if action not in {'apply', 'rollback'}:
        raise ValueError('unknown source audit release action')
    if not operation.is_relative_to(ROOT / 'test-artifacts') or operation.exists():
        raise ValueError('new operation directory under test-artifacts required')
    if production:
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(('127.0.0.1', 8000)) == 0:
                raise ValueError('maintenance required: use existing stop-vocab.ps1')
    if action == 'apply':
        validate_plan(database)
    else:
        if receipt is None or not receipt.resolve().is_relative_to(ROOT / 'test-artifacts'):
            raise ValueError('explicit local source-audit batch receipt required')
        undo = json.loads(receipt.read_bytes())
        if undo['plan_sha256'] != PLAN_SHA or Path(undo['database']).resolve() != database:
            raise ValueError('source audit receipt identity mismatch')
    before = protected.state(database)
    if before[0] != protected.REVISION:
        raise ValueError('existing 0016 extraction schema required; no implicit migration')
    operation.mkdir(parents=True)
    if protected.backup_database(database, operation) != before:
        raise ValueError('database changed before source-audit backup')
    if protected.authenticate(database, username, password) != identity:
        raise ValueError('administrator changed before update')

    def connect(path):
        check_code(code_sha)
        if protected.guard_database(path, production=production) != database:
            raise ValueError('source audit database identity changed')
        c = sqlite3.connect(database)
        c.row_factory = sqlite3.Row
        c.execute('pragma foreign_keys=on')
        return c

    def verify(c):
        row = c.execute('select id,username,role,is_active,password_hash from user where id=?', (identity[0],)).fetchone()
        if row is None or tuple(row) != identity:
            raise ValueError('administrator changed under write lock')

    if action == 'apply':
        receipt = operation / 'batch-undo.json'
        result = protected.rich.apply(database, PLAN, PLAN_SHA, receipt, _connect=connect, _verify=verify)
    else:
        result = protected.rich.rollback(database, receipt, _connect=connect, _verify=verify)
    if protected.state(database) != before:
        raise ValueError('old business table preservation failed')
    report = {'action': action, 'time': datetime.now(UTC).isoformat(), 'code_sha': code_sha,
              'plan_sha256': PLAN_SHA, 'administrator_id': identity[0], 'database': str(database),
              'result': result, 'receipt': str(receipt.resolve()), 'all_existing_tables_preserved': True,
              'preserved_tables': before[1], 'human_core_confirmation_added': 0,
              'integrity_check': 'ok', 'foreign_key_errors': 0}
    protected.write_new(operation / 'result.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['apply', 'rollback'])
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--operation', required=True, type=Path)
    parser.add_argument('--code-sha', required=True)
    parser.add_argument('--confirm', required=True)
    parser.add_argument('--admin', default='admin')
    parser.add_argument('--credential-file', type=Path)
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--production-update', action='store_true')
    args = parser.parse_args()
    if args.confirm != PLAN_SHA:
        raise ValueError('exact frozen audit plan digest required')
    check_code(args.code_sha)

    def execute(password):
        return authenticated_execute(database=args.database, code_sha=args.code_sha,
            username=args.admin, password=password, operation=args.operation,
            production=args.production_update, action=args.action, receipt=args.receipt)

    if args.credential_file:
        with protected.consume_local_credential(args.credential_file) as (username, password):
            if username != args.admin:
                raise ValueError('source audit local administrator mismatch')
            report = execute(password)
    else:
        report = execute(getpass.getpass('管理员当前口令（仅本机）：'))
    print(json.dumps({k:v for k,v in report.items() if k != 'preserved_tables'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
