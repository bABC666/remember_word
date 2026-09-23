"""The T8 staging acceptance checker's own machinery.

The three-account run itself is an operator procedure (it starts the application
against a copy of the live database), so it is not something a unit test pretends to
perform. What *can* be pinned is the part of the checker that would otherwise be taken
on trust:

* the copy step uses the SQLite online backup API, refuses to overwrite, and hands back
  a self-contained database (no WAL sidecar to lose);
* the "was the live database written?" fingerprint notices a write;
* the Origin header is attached to unsafe requests and can be deliberately omitted --
  the checker's whole CSRF contract depends on those two behaviours;
* a matrix that quietly loses a required item fails the run.
"""

from __future__ import annotations

import contextlib
import importlib.util
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = load_tool("staging_three_user_check")


# --- the copy step -----------------------------------------------------------


@pytest.fixture()
def wal_database(tmp_path: Path) -> Path:
    """A small database in WAL mode with committed content still in the -wal file."""
    database = tmp_path / "source.db"
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute("pragma journal_mode=wal")
        connection.execute("create table history_event (id integer primary key, payload text)")
        connection.execute("insert into history_event values (1, 'one')")
        connection.commit()
        # A second connection writes and commits without checkpointing, so the newest
        # row lives in the -wal file: exactly the copy a plain file copy would lose.
        with contextlib.closing(sqlite3.connect(database)) as writer:
            writer.execute("insert into history_event values (2, 'two')")
            writer.commit()
        assert connection.execute("select count(*) from history_event").fetchone()[0] == 2
    return database


def test_the_copy_uses_the_online_backup_api_and_is_self_contained(
    wal_database: Path, tmp_path: Path
) -> None:
    target = tmp_path / "copy.db"

    checker.online_backup(wal_database, target)

    # Committed content that was still in the WAL is in the copy.
    with contextlib.closing(sqlite3.connect(target)) as connection:
        assert connection.execute("select count(*) from history_event").fetchone()[0] == 2
        assert connection.execute("pragma integrity_check").fetchone()[0] == "ok"
        # The copy is one self-contained file: a restore cannot get the mode wrong.
        assert connection.execute("pragma journal_mode").fetchone()[0] != "wal"
    assert not target.with_name(target.name + "-wal").exists()
    assert not target.with_name(target.name + "-shm").exists()


def test_the_copy_refuses_to_overwrite_existing_evidence(wal_database: Path, tmp_path: Path) -> None:
    target = tmp_path / "copy.db"
    target.write_text("an earlier run's evidence", encoding="utf-8")

    with pytest.raises(SystemExit):
        checker.online_backup(wal_database, target)

    assert target.read_text(encoding="utf-8") == "an earlier run's evidence"


# --- the "was it written?" fingerprint ---------------------------------------


def test_the_database_fingerprint_notices_a_write(tmp_path: Path) -> None:
    database = tmp_path / "vocab.db"
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute("create table history_event (id integer primary key, payload text)")
        connection.execute("insert into history_event values (1, 'one')")
        connection.commit()

    before = checker.database_files(database)
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute("insert into history_event values (2, 'two')")
        connection.commit()
    after = checker.database_files(database)

    assert before != after
    assert before["vocab.db"]["sha256"] != after["vocab.db"]["sha256"]
    # Reading it back does not change the fingerprint.
    assert checker.database_files(database) == after


# --- the Origin contract -----------------------------------------------------


class Recorder(BaseHTTPRequestHandler):
    seen: ClassVar[list[dict[str, str]]] = []

    def _record(self) -> None:
        Recorder.seen.append(
            {"method": self.command, "origin": self.headers.get("Origin", ""), "path": self.path}
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    do_GET = _record  # type: ignore[assignment]
    do_POST = _record  # type: ignore[assignment]
    do_PUT = _record  # type: ignore[assignment]
    do_DELETE = _record  # type: ignore[assignment]

    def log_message(self, *_args) -> None:  # keep the test output quiet
        return


@pytest.fixture()
def recorded_server():
    Recorder.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def test_writes_carry_the_same_origin_and_reads_do_not_need_one(recorded_server: str) -> None:
    client = checker.Client(recorded_server)

    client.request("GET", "/api/health")
    client.request("PUT", "/api/settings", {"daily_new_words": 5})
    client.request("PUT", "/api/settings", {"daily_new_words": 5}, origin="http://evil.example")
    client.request("PUT", "/api/settings", {"daily_new_words": 5}, origin=None)

    assert Recorder.seen == [
        {"method": "GET", "origin": "", "path": "/api/health"},
        {"method": "PUT", "origin": recorded_server, "path": "/api/settings"},
        {"method": "PUT", "origin": "http://evil.example", "path": "/api/settings"},
        {"method": "PUT", "origin": "", "path": "/api/settings"},
    ]


# --- the matrix guard --------------------------------------------------------


def test_the_required_matrix_is_well_formed() -> None:
    labels = [label for label, _needle in checker.REQUIRED_MATRIX]
    needles = [needle for _label, needle in checker.REQUIRED_MATRIX]

    assert len(labels) >= 20
    assert len(set(labels)) == len(labels)
    assert all(needle.strip() for needle in needles)
    # Distinct needles, so a check can only ever satisfy one requirement.
    assert len(set(needles)) == len(needles)


def test_a_matrix_that_lost_a_case_is_detected() -> None:
    everything = [needle for _label, needle in checker.REQUIRED_MATRIX]

    assert checker.missing_matrix_items(everything) == []

    executed = [needle for needle in everything if "409" not in needle]
    missing = checker.missing_matrix_items(executed)

    assert len(missing) == 1
    assert "delete guard" in missing[0]

    # Nothing executed means nothing satisfied.
    assert len(checker.missing_matrix_items([])) == len(everything)


# --- the result collector ----------------------------------------------------


def test_a_failed_check_is_recorded_and_keeps_the_report_honest(capsys) -> None:
    results = checker.Results()
    results.check("a passing check", True)
    results.check("a failing check", False, "the detail an operator needs")

    assert [item["passed"] for item in results.checks] == [True, False]
    assert results.failures == ["a failing check: the detail an operator needs"]
    assert "FAIL" in capsys.readouterr().out


def test_equal_reports_both_values() -> None:
    results = checker.Results()

    assert results.equal("matches", 200, 200)
    assert not results.equal("differs", 200, 404)

    detail = results.checks[-1]["detail"]
    assert "404" in str(detail) and "200" in str(detail)


