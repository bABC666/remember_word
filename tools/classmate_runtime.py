"""Fixed local classmate lifecycle; never load .env or general launch settings."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8785
URL = f"http://127.0.0.1:{PORT}"


def _linked(path: Path) -> bool:
    return (path.is_symlink() or getattr(path, "is_junction", lambda: False)()
            or (path.is_file() and path.stat().st_nlink > 1))


def guarded_paths(root: Path) -> tuple[Path, Path]:
    root = root.resolve(strict=True)
    data = root / "data"
    target = data / "classmate-test"
    for path in [data, target]:
        if _linked(path):
            raise ValueError("link/junction/hardlink in classmate data path refused")
    for path in target.rglob("*") if target.is_dir() else []:
        if _linked(path):
            raise ValueError("link/junction/hardlink in classmate data path refused")
    if target.resolve() != root / "data/classmate-test":
        raise ValueError("classmate path escapes fixed data directory")
    return target, target / "classmate.sqlite3"


def isolated_env(root: Path) -> dict[str, str]:
    target, database = guarded_paths(root)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("VOCAB_", "DEEPSEEK_", "OPENAI_"))}
    env.update(VOCAB_DATA_DIR=str(target), VOCAB_DATABASE_PATH=str(database),
               VOCAB_ENABLE_OCR="false", VOCAB_COOKIE_SECURE="false",
               VOCAB_BOOTSTRAP_USERNAME="admin", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return env


def _process_record(target: Path) -> dict | None:
    path = target / "server.json"
    return json.loads(path.read_text("utf-8")) if path.exists() else None


def _own_process(record: dict, root: Path) -> bool:
    process_id = int(record["pid"])
    if os.name == "nt":
        command = ("[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
                   f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId = {process_id}'; "
                   "if ($p) { $p.CommandLine }")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                                capture_output=True, text=True, encoding="utf-8", check=True)
        line = result.stdout
    else:
        try:
            line = Path(f"/proc/{process_id}/cmdline").read_bytes().decode().replace("\0", " ")
        except FileNotFoundError:
            return False
    return (str(root / "tools/classmate_server.py") in line
            and "--instance=" + record["instance"] in line)


def reset_data(root: Path) -> None:
    target, _ = guarded_paths(root)
    record = _process_record(target)
    if record and _own_process(record, root):
        raise ValueError("stop the classmate service before Reset")
    # Resolve and check the exact allowed target immediately before deleting it.
    if target.resolve() != root.resolve() / "data/classmate-test":
        raise ValueError("reset path is not the exact synthetic directory")
    if target.exists():
        shutil.rmtree(target)


def setup(root: Path, *, reset: bool = False) -> dict:
    from app.classmate_dataset import BUNDLE_SHA256, build_database, verify_bundle
    target, database = guarded_paths(root)
    bundle = root / "assets/classmate-test"
    verify_bundle(bundle)
    if reset:
        reset_data(root)
    metadata = target / "dataset.json"
    if database.exists():
        if not metadata.is_file():
            raise ValueError("existing unknown database refused; explicit Reset required")
        result = json.loads(metadata.read_text("utf-8"))
        if result.get("package_sha256") != BUNDLE_SHA256:
            raise ValueError("existing dataset version differs; explicit Reset required")
        return {**result, "status": "retained"}
    env = isolated_env(root)
    os.environ.clear()
    os.environ.update(env)
    result = build_database(database, bundle)
    metadata.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return {**result, "status": "created"}


def stop(root: Path) -> str:
    target, _ = guarded_paths(root)
    record = _process_record(target)
    if record is None:
        return "no classmate process recorded"
    if _own_process(record, root):
        process_id = int(record["pid"])
        if os.name == "nt":
            subprocess.run(["taskkill.exe", "/PID", str(process_id), "/T", "/F"], check=True,
                           capture_output=True)
        else:
            import signal
            os.kill(process_id, signal.SIGTERM)
        for _ in range(100):
            if not _own_process(record, root):
                break
            time.sleep(0.1)
        else:
            raise ValueError("classmate process did not stop")
    # A reused PID is never terminated: the command and unique instance must match.
    (target / "server.json").unlink()
    return "classmate service stopped; dataset retained"


def start(root: Path) -> str:
    target, database = guarded_paths(root)
    if not database.is_file() or not (root / "frontend/dist/index.html").is_file():
        raise ValueError("run setup-classmate-test.bat first")
    from app.classmate_dataset import BUNDLE_SHA256
    metadata = json.loads((target / "dataset.json").read_text("utf-8"))
    if metadata.get("package_sha256") != BUNDLE_SHA256:
        raise ValueError("unknown classmate dataset refused")
    record = _process_record(target)
    if record and _own_process(record, root):
        return URL + " already running"
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", PORT))
        except OSError as error:
            raise ValueError("classmate port 8785 is occupied; no process was stopped") from error
    instance = uuid.uuid4().hex
    env = isolated_env(root)
    with (target / "server.log").open("ab") as log:
        child = subprocess.Popen(
            [sys.executable, str(root / "tools/classmate_server.py"), "--instance=" + instance],
            cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    record = {"pid": child.pid, "instance": instance}
    (target / "server.json").write_text(json.dumps(record), encoding="utf-8")
    try:
        for _ in range(150):
            if child.poll() is not None:
                raise ValueError("classmate service exited; inspect data/classmate-test/server.log")
            try:
                with urllib.request.urlopen(URL + "/api/health", timeout=1) as response:
                    if json.load(response).get("status") == "ok":
                        return URL
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        raise ValueError("classmate startup timed out")
    except BaseException:
        stop(root)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["setup", "start", "stop", "validate"])
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    if args.reset and args.action != "setup":
        parser.error("Reset is allowed only with setup")
    sys.path.insert(0, str(ROOT / "backend"))
    if args.action == "validate":
        guarded_paths(ROOT)
        print("fixed synthetic paths verified")
    elif args.action == "setup":
        print(json.dumps(setup(ROOT, reset=args.reset), ensure_ascii=False))
    else:
        print(start(ROOT) if args.action == "start" else stop(ROOT))


if __name__ == "__main__":
    main()
