"""Protected release for this display-only batch, with a NEW explicit plan digest.

No former plan or publication approval unlocks this operation. The operator must
provide the reviewed code commit, plan digest, and current administrator auth.
"""
import argparse
import getpass
import json
import socket
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
import netem_rich_release as protected
import netem_display_quality as quality

ROOT=protected.ROOT


def check_code(code_sha):
    protected.check_locked_code(code_sha)
    for path in [Path(__file__).resolve(),Path(quality.__file__).resolve(),
                 ROOT/'tools/phase29/netem_display_diagnosis.py',
                 ROOT/'tools/phase29/netem_display_fetch.py',ROOT/'tools/phase29/netem_display_browser.cjs',
                 ROOT/'tools/phase29/netem_display_verify.py',
                 ROOT/'tools/phase29/netem_pronunciation_context.py']:
        relative=path.relative_to(ROOT).as_posix()
        expected=subprocess.check_output(['git','rev-parse',f'{code_sha}:{relative}'],cwd=ROOT,text=True).strip()
        actual=subprocess.check_output(['git','hash-object',f'--path={relative}',str(path)],cwd=ROOT,text=True).strip()
        if expected!=actual:
            raise ValueError('display release executable changed')


def validate_plan(database, plan_path, checksum, production=False):
    plan_path=plan_path.resolve()
    if not plan_path.is_relative_to(ROOT/'test-artifacts') or protected.rich.sha(plan_path.read_bytes())!=checksum:
        raise ValueError('exact reviewed display plan fingerprint required')
    plan=json.loads(plan_path.read_bytes())
    if plan.get('display_version')!=quality.VERSION or plan.get('core_confirmation') is not False:
        raise ValueError('invalid display-only plan')
    records=plan['records']
    if not records or len({r['entry_id'] for r in records})!=len(records):
        raise ValueError('empty or duplicate display entries')
    if production and len(records)!=5528:
        raise ValueError('production requires complete reviewed5528-word plan')
    for name,meta in plan['source_files'].items():
        path=(ROOT/name).resolve()
        if not path.is_relative_to(ROOT/'test-artifacts') or protected.rich.sha(path.read_bytes())!=meta['sha256']:
            raise ValueError('display evidence fingerprint drift')
    folder=(ROOT/plan['evidence_directory']).resolve()
    if not folder.is_relative_to(ROOT/'test-artifacts'):
        raise ValueError('evidence path outside local artifacts')
    pages,ledger=quality.load_evidence(folder)
    _,_,original_ledger=protected.rich.local_sources()
    if {**original_ledger,**ledger}!=plan['source_files']:
        raise ValueError('evidence ledger not identical to reviewed plan')
    with protected.rich.read_only(database) as c:
        for r in records:
            entry=c.execute('select * from lexicon_entry where id=?',(r['entry_id'],)).fetchone()
            existing=c.execute('select * from entry_dictionary_extraction where lexicon_entry_id=?',(r['entry_id'],)).fetchone()
            if (entry is None or existing is None or entry['lexicon_id']!=plan['lexicon_id']
                or protected.rich.digest(protected.rich.original_baseline(entry))!=r['baseline_sha256']
                or protected.rich.digest(json.loads(existing['payload']))!=existing['payload_sha256']
                or existing['payload_sha256'] not in {r['baseline_extraction_sha256'],r['payload_sha256']}):
                raise ValueError('display baseline drift')
            original=json.loads(existing['payload']);original.pop('learning_display',None)
            after=dict(r['payload']);profile=after.pop('learning_display',{})
            if (after!=original or profile.get('baseline_sha256')!=quality.digest(original)
                or profile.get('version')!=quality.VERSION or profile.get('core_confirmation') is not False
                or quality.digest(r['payload'])!=r['payload_sha256']):
                raise ValueError('display plan changed raw extraction or confirmation')
            regenerated=quality.projection({'payload':original,'word':entry['word']},pages.get(entry['word']))
            if quality.digest(regenerated)!=quality.digest(profile):
                raise ValueError('display projection differs from locked source/context algorithm')
    return plan


def authenticated_execute(*, database, plan_path, checksum, code_sha, username,
                          password, operation, production=False, action='apply', receipt=None,
                          receipt_sha256=None):
    check_code(code_sha)
    database=protected.guard_database(database,production=production)
    identity=protected.authenticate(database,username,password)
    if action not in {'apply','rollback'}:
        raise ValueError('unknown action')
    operation=operation.resolve()
    if operation.exists() or not operation.is_relative_to(ROOT/'test-artifacts'):
        raise ValueError('fresh local operation directory required')
    if production:
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(('127.0.0.1',8000))==0:
                raise ValueError('maintenance required; stop formal service first')
    if action=='apply':
        validate_plan(database,plan_path,checksum,production)
    else:
        if not receipt or not receipt.resolve().is_relative_to(ROOT/'test-artifacts'):
            raise ValueError('explicit local undo receipt required')
        validate_undo(database,plan_path,checksum,receipt,receipt_sha256)
    before=protected.state(database)
    if before[0]!=protected.REVISION:
        raise ValueError('display update performs no schema migration')
    operation.mkdir(parents=True)
    if protected.backup_database(database,operation)!=before:
        raise ValueError('database changed during backup')
    def connect(path):
        check_code(code_sha)
        if protected.guard_database(path,production=production)!=database:
            raise ValueError('database identity changed')
        c=sqlite3.connect(database);c.row_factory=sqlite3.Row;c.execute('pragma foreign_keys=on')
        return c
    def verify(c):
        current=c.execute('select id,username,role,is_active,password_hash from user where id=?',(identity[0],)).fetchone()
        if current is None or tuple(current)!=identity:
            raise ValueError('administrator changed under write lock')
        if action=='apply':
            validate_plan(database,plan_path,checksum,production)
        else:
            validate_undo(database,plan_path,checksum,receipt,receipt_sha256)
    if action=='apply':
        receipt=operation/'batch-undo.json'
        result=protected.rich.apply(database,plan_path,checksum,receipt,_connect=connect,_verify=verify)
    else:
        result=protected.rich.rollback(database,receipt,_connect=connect,_verify=verify)
    if protected.state(database)!=before:
        raise ValueError('protected table preservation failed')
    report={'action':action,'code_sha':code_sha,'plan_sha256':checksum,'time':datetime.now(UTC).isoformat(),
        'result':result,'receipt':str(receipt),'protected_tables_preserved':True,
        'receipt_sha256':protected.rich.sha(receipt.read_bytes()),
        'human_core_confirmations_added':0,'preserved_tables':before[1]}
    protected.write_new(operation/'result.json',report)
    return report


def validate_undo(database,plan_path,checksum,receipt,receipt_sha256):
    if protected.rich.sha(plan_path.read_bytes())!=checksum:
        raise ValueError('rollback plan fingerprint mismatch')
    body=receipt.read_bytes()
    if not receipt_sha256 or protected.rich.sha(body)!=receipt_sha256:
        raise ValueError('exact batch receipt fingerprint required')
    undo=json.loads(body);plan=json.loads(plan_path.read_bytes())
    if undo['plan_sha256']!=checksum or Path(undo['database']).resolve()!=database.resolve():
        raise ValueError('wrong batch or database receipt')
    records={r['entry_id']:r for r in plan['records']}
    if len({item['entry_id'] for item in undo['undo']})!=len(undo['undo']):
        raise ValueError('duplicate undo entries')
    with protected.rich.read_only(database) as c:
        for item in undo['undo']:
            r=records.get(item['entry_id']);before=item['before']
            if (r is None or before is None or item['after_sha256']!=r['payload_sha256']
                or before['lexicon_entry_id']!=item['entry_id']
                or before['parser_version']!=plan['parser_version']
                or before['payload_sha256']!=r['baseline_extraction_sha256']
                or quality.digest(json.loads(before['payload']))!=before['payload_sha256']):
                raise ValueError('undo original baseline fingerprint mismatch')
            original=json.loads(before['payload']);original.pop('learning_display',None)
            planned=dict(r['payload']);planned.pop('learning_display',None)
            if original!=planned:
                raise ValueError('rollback would change original extraction')
            current=c.execute('select * from entry_dictionary_extraction where lexicon_entry_id=?',(item['entry_id'],)).fetchone()
            if current is None or current['payload_sha256'] not in {r['payload_sha256'],before['payload_sha256']}:
                raise ValueError('later extraction changed; rollback refused')
    return undo


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['apply','rollback']);p.add_argument('--database',type=Path,required=True)
    p.add_argument('--plan',type=Path,required=True);p.add_argument('--confirm',required=True)
    p.add_argument('--code-sha',required=True);p.add_argument('--operation',type=Path,required=True)
    p.add_argument('--admin',default='admin');p.add_argument('--credential-file',type=Path)
    p.add_argument('--receipt',type=Path);p.add_argument('--production-update',action='store_true')
    p.add_argument('--receipt-sha256')
    a=p.parse_args()
    def execute(password):
        return authenticated_execute(database=a.database,plan_path=a.plan,checksum=a.confirm,
          code_sha=a.code_sha,username=a.admin,password=password,operation=a.operation,
          production=a.production_update,action=a.action,receipt=a.receipt,receipt_sha256=a.receipt_sha256)
    if a.credential_file:
        with protected.consume_local_credential(a.credential_file) as (username,password):
            if username!=a.admin:
                raise ValueError('local credential administrator mismatch')
            report=execute(password)
    else:
        report=execute(getpass.getpass('管理员当前口令（仅本机）：'))
    print(json.dumps({k:v for k,v in report.items() if k!='preserved_tables'},ensure_ascii=False,indent=2))
