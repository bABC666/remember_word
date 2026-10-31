"""Rehearse the lookup migration, back up at stop time, publish, and verify."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

stage = Path(__file__).resolve().parent
assert stage == Path('/opt/shici/reading-review-20261030')
root = Path('/opt/shici/current')
database = Path('/var/lib/shici/vocab.db')
backup = stage / 'backup'
payload = stage / 'payload'
package = stage / 'patch.tar.gz'
assert hashlib.sha256(package.read_bytes()).hexdigest() == (stage / 'package.sha256').read_text().strip()
assert not payload.exists() and not backup.exists(), 'Inspect the prior operation; do not rerun blindly'
with tarfile.open(package) as archive:
    manifest = json.load(archive.extractfile('manifest.json'))
    assert len(archive.getmembers()) == len(manifest['files']) + 1
    assert {item.name for item in archive.getmembers()} == {'manifest.json', *manifest['files']}
    for item in archive.getmembers():
        assert item.isfile() and not item.name.startswith('/') and '..' not in Path(item.name).parts
    for name, digest in manifest['files'].items():
        assert name in manifest['baseline'] or name == 'frontend/dist/index.html' or (
            name.startswith('frontend/dist/assets/index-') and name.endswith(('.js', '.css')))
        path = payload / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(archive.extractfile(name).read())
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        if name.endswith('.py'):
            compile(path.read_text(), name, 'exec')
for name, digest in manifest['baseline'].items():
    original = root / name
    assert not original.is_symlink()
    if digest is None:
        assert not original.exists(), 'New file already exists: ' + name
    else:
        assert hashlib.sha256(original.read_bytes()).hexdigest() == digest, 'Baseline changed: ' + name
subprocess.run(['systemctl', 'is-active', '--quiet', 'shici'], check=True)
protected = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (
    Path('/etc/shici/shici.env'), root / 'backend/app/config.py')}


def copy_database(source, destination):
    with closing(sqlite3.connect('file:' + str(source) + '?mode=ro', uri=True)) as incoming:
        with closing(sqlite3.connect(destination)) as outgoing:
            incoming.backup(outgoing)
            outgoing.commit()


def fingerprint(path):
    with closing(sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True)) as connection:
        assert connection.execute('pragma integrity_check').fetchone()[0] == 'ok'
        assert connection.execute('pragma foreign_key_check').fetchall() == []
        tables = [row[0] for row in connection.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%' and name != 'alembic_version'")]
        answer = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            rows = sorted(json.dumps(row, ensure_ascii=False, default=str) for row in connection.execute('select * from ' + quoted))
            answer[table] = {'rows': len(rows), 'sha256': hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
        return answer


def migrate(backend, path, log_name):
    environment = dict(os.environ, VOCAB_DATA_DIR=str(path.parent / 'migration-data'),
                       VOCAB_DATABASE_PATH=str(path), VOCAB_ENABLE_OCR='false')
    result = subprocess.run([sys.executable, '-m', 'alembic', '-c', str(backend / 'alembic.ini'),
        '-x', 'db_url=sqlite:///' + path.as_posix(), 'upgrade', 'head'], cwd=backend,
        env=environment, capture_output=True, text=True)
    (stage / log_name).write_text(result.stdout + result.stderr)
    assert result.returncode == 0, 'Migration failed; see ' + log_name


rehearsal = stage / 'rehearsal/backend'
shutil.copytree(root / 'backend/app', rehearsal / 'app', ignore=shutil.ignore_patterns('__pycache__'))
shutil.copytree(root / 'backend/alembic', rehearsal / 'alembic', ignore=shutil.ignore_patterns('__pycache__'))
shutil.copy2(root / 'backend/alembic.ini', rehearsal / 'alembic.ini')
for name in manifest['baseline']:
    if name.startswith('backend/'):
        target = stage / 'rehearsal' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(payload / name, target)
verification = stage / 'verification.db'
copy_database(database, verification)
before_rehearsal = fingerprint(verification)
migrate(rehearsal, verification, 'rehearsal-migration.log')
assert fingerprint(verification) == before_rehearsal, 'Rehearsal changed stored rows'
probe = subprocess.run([sys.executable, str(stage / 'probe.py'), str(verification), str(rehearsal)],
                       cwd=rehearsal, capture_output=True, text=True)
(stage / 'probe.log').write_text(probe.stdout + probe.stderr)
assert probe.returncode == 0, 'Isolated API probe failed; see probe.log'
print(probe.stdout.strip(), flush=True)

backup.mkdir(mode=0o700)
for name, digest in manifest['baseline'].items():
    if digest is not None:
        target = backup / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, target)
for name in ('frontend/dist/index.html', 'DEPLOYMENT-MANIFEST.json'):
    target = backup / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / name, target)
account = pwd.getpwnam('shici')


def publish(source, target):
    temporary = target.with_name('.' + target.name + '.reading-review.tmp')
    with temporary.open('xb') as output:
        output.write(source.read_bytes())
        output.flush()
        os.fsync(output.fileno())
    temporary.chmod(0o644)
    os.chown(temporary, account.pw_uid, account.pw_gid)
    temporary.replace(target)


started = False
backed_up = False
migrated_snapshot = None
try:
    subprocess.run(['systemctl', 'stop', 'shici'], check=True)
    copy_database(database, backup / 'before.db')
    original_snapshot = fingerprint(backup / 'before.db')
    backed_up = True
    for name in sorted(manifest['files'], key=lambda name: name == 'frontend/dist/index.html'):
        publish(payload / name, root / name)
    migrate(root / 'backend', database, 'production-migration.log')
    migrated_snapshot = fingerprint(database)
    assert migrated_snapshot == original_snapshot, 'Production migration changed stored rows'
    (stage / 'preservation.json').write_text(json.dumps({'before': original_snapshot, 'after': migrated_snapshot}, indent=2))
    assert protected == {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in protected}
    subprocess.run(['systemctl', 'start', 'shici'], check=True)
    started = True
    for attempt in range(20):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3) as response:
                assert json.load(response)['status'] == 'ok'
            break
        except (OSError, AssertionError):
            if attempt == 19:
                raise
            time.sleep(1)
    for name, digest in manifest['files'].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
        if name.startswith('frontend/dist/'):
            route = '/' if name.endswith('/index.html') else '/' + name.removeprefix('frontend/dist/')
            with urllib.request.urlopen('http://127.0.0.1' + route, timeout=10) as response:
                assert response.status == 200 and hashlib.sha256(response.read()).hexdigest() == digest
    try:
        urllib.request.urlopen('http://127.0.0.1:8000/api/study/today', timeout=5)
        raise AssertionError('Anonymous study access allowed')
    except urllib.error.HTTPError as error:
        assert error.code == 401
    deployed = json.loads((root / 'DEPLOYMENT-MANIFEST.json').read_text())
    deployed['files'].update(manifest['files'])
    deployed.setdefault('overlays', []).append(manifest)
    updated = stage / 'DEPLOYMENT-MANIFEST.updated.json'
    updated.write_text(json.dumps(deployed, indent=2))
    publish(updated, root / 'DEPLOYMENT-MANIFEST.json')
    receipt = {'status': 'ok', 'schema': manifest['to_schema'], 'feature': manifest['feature'],
               'backup': str(backup), 'all_existing_rows_preserved': True,
               'isolated_api_probe': 'passed', 'paid_ai_calls': 0, 'homepage_and_assets': 200,
               'backend_health': 'ok', 'api_configuration_preserved': True}
    (stage / 'result.json').write_text(json.dumps(receipt, indent=2))
    print('READING_REVIEW_DEPLOYED', json.dumps(receipt), flush=True)
except BaseException:
    subprocess.run(['systemctl', 'stop', 'shici'], check=False)
    if started and migrated_snapshot is not None:
        current = fingerprint(database)
        without_sessions = lambda values: {key: value for key, value in values.items() if key != 'user_session'}
        if without_sessions(current) != without_sessions(migrated_snapshot):
            subprocess.run(['systemctl', 'start', 'shici'], check=False)
            print('ROLLBACK_REFUSED_NEW_USER_WRITES: keep new schema/code and inspect', flush=True)
            raise
    if backed_up:
        copy_database(backup / 'before.db', database)
    for name, digest in manifest['baseline'].items():
        target = root / name
        assert target.resolve().is_relative_to(root.resolve())
        if digest is None:
            if target.exists():
                target.unlink()
        else:
            publish(backup / name, target)
    for name in ('frontend/dist/index.html', 'DEPLOYMENT-MANIFEST.json'):
        publish(backup / name, root / name)
    subprocess.run(['systemctl', 'start', 'shici'], check=True)
    print('ORIGINAL_CODE_AND_DATABASE_RESTORED', flush=True)
    raise
