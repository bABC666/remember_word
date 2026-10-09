"""Tests for the server stop path in ``scripts/stop-vocab.ps1``.

The old script read ``data/server.pid`` and nothing else. On 2026-09-22 that file
named a process which no longer existed, so the script deleted it and printed
"Shici server stopped." while the real server kept running and holding the
database open -- exactly the state a production migration must never start in.

These tests pin the three observations the stop path has to combine (the pid file,
the listener on the port, and this checkout's python processes) and the four
states an operator can actually meet:

1. the pid file is correct;
2. the pid file is stale (the process is gone);
3. the pid file is wrong but something is listening on the port;
4. nothing is running at all.

They also pin the safety property that makes the port check safe to trust: a pid
file naming a live process that is *not* ours is reported and left alone.

Everything runs against a throwaway checkout and a throwaway port, so the suite
can never signal a real server or the real user data directory.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STOP_SCRIPT = PROJECT_ROOT / "scripts" / "stop-vocab.ps1"

#: Binds an ephemeral port, reports it to the caller, then holds it open.
FAKE_SERVER = """
import socket, sys, time

sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", 0))
with open(sys.argv[1], "w") as handle:
    handle.write(str(sock.getsockname()[1]))
sock.listen(8)
time.sleep(300)
"""

WINDOWS_POWERSHELL = (
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)


def find_powershell() -> str | None:
    for name in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
        found = shutil.which(name)
        if found:
            return found
    if WINDOWS_POWERSHELL.exists():
        return str(WINDOWS_POWERSHELL)
    return None


pytestmark = pytest.mark.skipif(
    find_powershell() is None, reason="no PowerShell host is available"
)


@pytest.fixture(scope="module")
def powershell() -> str:
    host = find_powershell()
    assert host is not None
    return host


@pytest.fixture(scope="module")
def fake_checkout(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A throwaway checkout whose python executable lives under its own root.

    ``-CheckoutRoot`` is pointed here for every test, so the script's scan for
    "this checkout's python processes" can never reach the real checkout -- and
    therefore can never signal a real server.
    """
    root = tmp_path_factory.mktemp("stop-vocab") / "checkout"
    (root / "scripts").mkdir(parents=True)
    (root / "data").mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "venv",
            "--without-pip",
            str(root / "backend" / ".venv"),
        ],
        check=True,
        capture_output=True,
    )
    return root


@pytest.fixture
def spawned():
    """Track every process a test starts so it is always reaped."""
    processes: list[subprocess.Popen] = []
    yield processes
    for process in processes:
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass


def wait_for(predicate, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def port_is_listening(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def free_port() -> int:
    """An ephemeral port nothing is listening on."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def dead_pid() -> int:
    """A pid that is guaranteed not to exist any more."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=30)
    return process.pid


def start_fake_server(fake_checkout: Path, tmp_path: Path, spawned: list) -> tuple[subprocess.Popen, int]:
    """Start a listener that belongs to the throwaway checkout."""
    python = fake_checkout / "backend" / ".venv" / "Scripts" / "python.exe"
    assert python.exists(), f"throwaway virtualenv is missing {python}"
    port_file = tmp_path / "fake-server.port"
    process = subprocess.Popen([str(python), "-c", FAKE_SERVER, str(port_file)])
    spawned.append(process)

    ready = wait_for(lambda: port_file.exists() and port_file.read_text().strip() != "")
    assert ready, "the fake server never reported its port"
    return process, int(port_file.read_text().strip())


def run_stop(
    powershell: str,
    fake_checkout: Path,
    data_root: Path,
    *,
    port: int,
    timeout: float = 120.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(STOP_SCRIPT),
            "-CheckoutRoot",
            str(fake_checkout),
            "-DataRoot",
            str(data_root),
            "-Port",
            str(port),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )


def can_inspect_port_owner(powershell: str, port: int, expected_pid: int) -> bool:
    """Probe the same Windows API used by stop-vocab before relying on its warning."""
    command = (
        f"Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction Stop "
        "| Select-Object -ExpandProperty OwningProcess -Unique"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and str(expected_pid) in result.stdout.split()


def write_pid_file(data_root: Path, value: object) -> Path:
    data_root.mkdir(parents=True, exist_ok=True)
    pid_file = data_root / "server.pid"
    pid_file.write_text(str(value))
    return pid_file


# --- case 1: the pid file is correct ---------------------------------------


def test_correct_pid_is_stopped_and_success_is_verified(
    powershell: str, fake_checkout: Path, tmp_path: Path, spawned: list
) -> None:
    process, port = start_fake_server(fake_checkout, tmp_path, spawned)
    data_root = tmp_path / "data"
    pid_file = write_pid_file(data_root, process.pid)
    assert port_is_listening(port)

    result = run_stop(powershell, fake_checkout, data_root, port=port)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Shici server stopped." in result.stdout
    assert "no listener" in result.stdout
    assert wait_for(lambda: not port_is_listening(port)), "the port was never released"
    assert wait_for(lambda: process.poll() is not None), "the server survived"
    assert not pid_file.exists(), "the pid file should be removed once stopping is proven"


# --- case 2: the pid file is stale -----------------------------------------


def test_stale_pid_file_is_never_reported_as_a_stop(
    powershell: str, fake_checkout: Path, tmp_path: Path
) -> None:
    data_root = tmp_path / "data"
    pid_file = write_pid_file(data_root, dead_pid())
    port = free_port()
    assert not port_is_listening(port)

    result = run_stop(powershell, fake_checkout, data_root, port=port)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "No running Shici server found." in result.stdout
    # Nothing was stopped, so the script must not claim it stopped anything.
    assert "Shici server stopped." not in result.stdout
    assert not pid_file.exists(), "a stale pid file should be cleaned up"


# --- case 3: the pid file is wrong but the port is held --------------------


def test_wrong_pid_with_a_live_listener_reports_the_real_process(
    powershell: str, fake_checkout: Path, tmp_path: Path, spawned: list
) -> None:
    process, port = start_fake_server(fake_checkout, tmp_path, spawned)
    data_root = tmp_path / "data"
    stale_pid = dead_pid()
    write_pid_file(data_root, stale_pid)
    owner_visible = can_inspect_port_owner(powershell, port, process.pid)

    result = run_stop(powershell, fake_checkout, data_root, port=port)

    assert result.returncode == 0, result.stdout + result.stderr
    # The real process is named even when the pid file lies and the port API is denied.
    assert f"= {stale_pid}" in result.stdout, result.stdout
    assert str(process.pid) in result.stdout, result.stdout
    if owner_visible:
        assert "WARNING" in result.stdout
        assert "does not exist" in result.stdout
    assert "Shici server stopped." in result.stdout
    assert wait_for(lambda: not port_is_listening(port)), "the port was never released"
    assert wait_for(lambda: process.poll() is not None), "the real listener survived"


def test_missing_pid_file_with_a_live_listener_reports_the_real_process(
    powershell: str, fake_checkout: Path, tmp_path: Path, spawned: list
) -> None:
    process, port = start_fake_server(fake_checkout, tmp_path, spawned)
    data_root = tmp_path / "data"
    data_root.mkdir()
    owner_visible = can_inspect_port_owner(powershell, port, process.pid)

    result = run_stop(powershell, fake_checkout, data_root, port=port)

    assert result.returncode == 0, result.stdout + result.stderr
    assert str(process.pid) in result.stdout, result.stdout
    assert "pid file  : missing" in result.stdout
    if owner_visible:
        assert "server.pid is missing" in result.stdout
    assert "Shici server stopped." in result.stdout
    assert wait_for(lambda: not port_is_listening(port)), "the port was never released"
    assert wait_for(lambda: process.poll() is not None), "the real listener survived"


# --- case 4: no service ----------------------------------------------------


def test_no_service_reports_nothing_running(
    powershell: str, fake_checkout: Path, tmp_path: Path
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    port = free_port()

    result = run_stop(powershell, fake_checkout, data_root, port=port)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "No running Shici server found." in result.stdout
    assert "Shici server stopped." not in result.stdout


# --- the safety property: never signal a process that is not ours ----------


def test_pid_naming_an_unrelated_live_process_is_left_alone(
    powershell: str, fake_checkout: Path, tmp_path: Path, spawned: list
) -> None:
    """A recycled pid must not cost an unrelated process its life."""
    unrelated = subprocess.Popen(
        [powershell, "-NoProfile", "-Command", "Start-Sleep -Seconds 300"]
    )
    spawned.append(unrelated)
    time.sleep(0.5)
    assert unrelated.poll() is None

    data_root = tmp_path / "data"
    write_pid_file(data_root, unrelated.pid)
    port = free_port()

    result = run_stop(powershell, fake_checkout, data_root, port=port)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "not a process from this checkout" in result.stdout
    assert "It will not be stopped." in result.stdout
    time.sleep(1.0)
    assert unrelated.poll() is None, "the script killed an unrelated process"
