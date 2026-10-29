"""Guarded code-only update; rehearse in a DB copy and roll back code on failure."""
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
assert stage == Path('/opt/shici/study-fixes-20261028')
root = Path('/opt/shici/current')
backup = stage / 'backup'
payload = stage / 'payload'
package = stage / 'patch.tar.gz'
expected = (stage / 'package.sha256').read_text().strip()
assert hashlib.sha256(package.read_bytes()).hexdigest() == expected
assert not backup.exists() and not payload.exists(), 'This release already ran; inspect its result first'
runtime = {'backend/app/services/study.py', 'backend/app/api/study.py',
           'frontend/src/pages/StudyPage.tsx', 'frontend/src/styles.css'}
with tarfile.open(package) as archive:
    manifest = json.load(archive.extractfile('manifest.json'))
    assert set(manifest['baseline']) == runtime
    assert len(archive.getmembers()) == len(manifest['files']) + 1
    assert {item.name for item in archive.getmembers()} == {'manifest.json', *manifest['files']}
    for item in archive.getmembers():
        assert item.isfile() and not item.name.startswith('/') and '..' not in Path(item.name).parts
    for name, digest in manifest['files'].items():
        assert name in runtime | {'frontend/dist/index.html'} or (
            name.startswith('frontend/dist/assets/index-') and name.endswith(('.js', '.css')))
        target = payload / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.extractfile(name).read())
        assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
        if name.endswith('.py'):
            compile(target.read_text(), name, 'exec')
for name, digest in manifest['baseline'].items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, 'Baseline changed: ' + name
subprocess.run(['systemctl', 'is-active', '--quiet', 'shici'], check=True)
protected = [Path('/etc/shici/shici.env'), root / 'backend/app/config.py']
protected_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
backup.mkdir(mode=0o700)
for name in (*runtime, 'frontend/dist/index.html', 'DEPLOYMENT-MANIFEST.json'):
    target = backup / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / name, target)
with sqlite3.connect('file:/var/lib/shici/vocab.db?mode=ro', uri=True) as live:
    with sqlite3.connect(backup / 'before.db') as copy:
        live.backup(copy)
with sqlite3.connect(backup / 'before.db') as before:
    assert before.execute('pragma integrity_check').fetchone()[0] == 'ok'
    with sqlite3.connect(stage / 'verification.db') as copy:
        before.backup(copy)
rehearsal = stage / 'rehearsal/backend'
shutil.copytree(root / 'backend/app', rehearsal / 'app', ignore=shutil.ignore_patterns('__pycache__'))
shutil.copytree(root / 'backend/alembic', rehearsal / 'alembic', ignore=shutil.ignore_patterns('__pycache__'))
shutil.copy2(root / 'backend/alembic.ini', rehearsal / 'alembic.ini')
for name in runtime:
    if name.startswith('backend/'):
        shutil.copy2(payload / name, stage / 'rehearsal' / name)
probe = subprocess.run([sys.executable, str(stage / 'probe.py'), str(stage / 'verification.db'), str(rehearsal)],
                       cwd=rehearsal, capture_output=True, text=True)
(stage / 'probe.log').write_text(probe.stdout + probe.stderr)
assert probe.returncode == 0, 'Isolated API rehearsal failed; see probe.log'
print(probe.stdout.strip(), flush=True)
account = pwd.getpwnam('shici')


def publish(source, target):
    temporary = target.with_name('.' + target.name + '.study-fixes.tmp')
    with temporary.open('xb') as output:
        output.write(source.read_bytes())
        output.flush()
        os.fsync(output.fileno())
    temporary.chmod(0o644)
    os.chown(temporary, account.pw_uid, account.pw_gid)
    temporary.replace(target)


def health():
    with urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5) as response:
        assert json.load(response)['status'] == 'ok'


try:
    subprocess.run(['systemctl', 'stop', 'shici'], check=True)
    for name in sorted(manifest['files'], key=lambda name: name == 'frontend/dist/index.html'):
        publish(payload / name, root / name)
    subprocess.run(['systemctl', 'start', 'shici'], check=True)
    for attempt in range(20):
        try:
            health()
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
        raise AssertionError('Anonymous study access unexpectedly allowed')
    except urllib.error.HTTPError as error:
        assert error.code == 401
    assert protected_hashes == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    deployed = json.loads((root / 'DEPLOYMENT-MANIFEST.json').read_text())
    deployed['files'].update(manifest['files'])
    deployed.setdefault('overlays', []).append(manifest)
    updated = stage / 'DEPLOYMENT-MANIFEST.updated.json'
    updated.write_text(json.dumps(deployed, indent=2))
    publish(updated, root / 'DEPLOYMENT-MANIFEST.json')
    receipt = dict(status='ok', feature=manifest['feature'], backup=str(backup),
                   isolated_api_probe='passed', homepage_and_assets=200, backend_health='ok',
                   anonymous_study_access=401, database_migrations=False,
                   api_configuration_preserved=True, old_frontend_assets_retained=True)
    (stage / 'result.json').write_text(json.dumps(receipt, indent=2))
    print('STUDY_FIXES_DEPLOYED', json.dumps(receipt), flush=True)
except BaseException:
    subprocess.run(['systemctl', 'stop', 'shici'], check=False)
    for name in (*runtime, 'frontend/dist/index.html', 'DEPLOYMENT-MANIFEST.json'):
        publish(backup / name, root / name)
    subprocess.run(['systemctl', 'start', 'shici'], check=True)
    print('ORIGINAL_STUDY_CODE_RESTORED', flush=True)
    raise
