"""Rehearse 0007 -> code head and private file imports on an online production backup.

Only the SQLite backup source is opened against production, with mode=ro. All
migrations, accounts and HTTP requests target the disposable staging clone.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
SOURCE = ROOT / "data" / "vocab.db"
CLONE = ROOT / "data" / "staging" / "private-file-acceptance.db"
REPORT = ROOT / "data" / "recovery" / "private-file-acceptance-2026-10-03.json"
ROLLBACK_PROBE = ROOT / "data" / "staging" / "private-file-rollback-probe.db"
REVISIONS = [
    "0008_public_lexicon_import", "0009_entry_concise_meaning",
    "0010_entry_source_revision", "0011_entry_concise_meaning_pos",
    "0012_source_wikitext_line", "0013_concise_meaning_wikitext_binding",
    "0014_selected_lexicon",
]


def connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def state(connection: sqlite3.Connection) -> dict:
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    rows = {}
    for table in tables:
        columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
        records = connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
        rows[table] = {"columns": columns, "records": records}
    return {
        "revision": connection.execute("SELECT version_num FROM alembic_version").fetchone()[0],
        "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
        "foreign_key_errors": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
        "tables": rows,
    }


def old_rows_preserved(before: dict, after: dict) -> list[str]:
    drift = []
    for table, original in before["tables"].items():
        if table == "alembic_version":
            continue
        if table not in after["tables"]:
            drift.append(table + ": missing")
            continue
        current = after["tables"][table]
        indices = [current["columns"].index(column) for column in original["columns"]]
        projected = [tuple(row[index] for index in indices) for row in current["records"]]
        if projected != original["records"]:
            drift.append(table + ": row change")
    return drift


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(label: str, condition: bool, failures: list[str]) -> None:
    if not condition:
        failures.append(label)


def main() -> int:
    failures: list[str] = []
    evidence: dict = {"git_head": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip(), "migration_steps": [], "checks": {}}
    if CLONE.exists():
        raise RuntimeError(f"Refusing to overwrite existing clone: {CLONE}")
    CLONE.parent.mkdir(parents=True, exist_ok=True)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    source_files = {path.name: file_sha(path) for path in (
        SOURCE, SOURCE.with_name(SOURCE.name + "-wal")
    ) if path.exists()}
    with connect_ro(SOURCE) as source:
        before = state(source)
        evidence["production_before"] = {
            "revision": before["revision"], "integrity": before["integrity"],
            "foreign_key_errors": before["foreign_key_errors"],
            "table_counts": {key: len(value["records"]) for key, value in before["tables"].items()},
            "source_file_sha256": source_files,
        }
        require("production revision is not 0007", before["revision"] == "0007_bridge_foreign_keys", failures)
        require("production integrity failed", before["integrity"] == "ok", failures)
        require("production foreign keys failed", before["foreign_key_errors"] == 0, failures)
        if failures:
            raise RuntimeError("Preflight failed: " + "; ".join(failures))
        with sqlite3.connect(CLONE) as clone:
            source.backup(clone)
        source_after_backup = state(source)
    with connect_ro(CLONE) as clone:
        copied = state(clone)
    evidence["backup_source_read_consistency"] = {
        "pre_backup_matches_clone": before == copied,
        "post_backup_matches_clone": source_after_backup == copied,
    }
    if copied != source_after_backup:
        print("backup comparison:", {key: (source_after_backup[key], copied[key])
              for key in ("revision", "integrity", "foreign_key_errors")
              if source_after_backup[key] != copied[key]})
        print("different tables:", [(table, len(source_after_backup["tables"][table]["records"]),
              len(copied["tables"].get(table, {}).get("records", [])))
              for table in source_after_backup["tables"]
              if source_after_backup["tables"][table] != copied["tables"].get(table)])
    require("online backup differs from source after backup", copied == source_after_backup, failures)
    before = copied
    evidence["backup"] = {"method": "sqlite3.Connection.backup", "sha256": file_sha(CLONE),
                          "bytes": CLONE.stat().st_size, "logical_match": copied == before}
    if failures:
        raise RuntimeError("Backup verification failed: " + "; ".join(failures))

    env = dict(os.environ)
    env.update({"VOCAB_DATA_DIR": str(CLONE.parent), "VOCAB_REAL_DATA_DIR": str(ROOT / "data"),
                "VOCAB_DATABASE_PATH": str(CLONE), "VOCAB_ENABLE_OCR": "false",
                "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    for revision in REVISIONS:
        command = [sys.executable, "-m", "alembic", "-c", str(BACKEND / "alembic.ini"),
                   "-x", f"db_url=sqlite:///{CLONE.as_posix()}", "upgrade", revision]
        result = subprocess.run(command, cwd=BACKEND, env=env, text=True, check=False,
                                capture_output=True, encoding="utf-8", errors="replace")
        with connect_ro(CLONE) as clone:
            observed = state(clone)
        evidence["migration_steps"].append({"target": revision, "exit_code": result.returncode,
                                             "observed": observed["revision"],
                                             "output_tail": (result.stdout + result.stderr)[-1200:]})
        if result.returncode or observed["revision"] != revision:
            failures.append("migration failed: " + revision)
            break
    if not failures:
        with connect_ro(CLONE) as clone:
            migrated = state(clone)
        evidence["migration_result"] = {"revision": migrated["revision"],
            "integrity": migrated["integrity"], "foreign_key_errors": migrated["foreign_key_errors"],
            "old_row_drift": old_rows_preserved(before, migrated),
            "table_counts": {key: len(value["records"]) for key, value in migrated["tables"].items()}}
        require("old rows changed by migration", not evidence["migration_result"]["old_row_drift"], failures)
        require("post migration integrity failed", migrated["integrity"] == "ok", failures)
        require("post migration foreign keys failed", migrated["foreign_key_errors"] == 0, failures)
    if not failures:
        os.environ.update({key: env[key] for key in ("VOCAB_DATA_DIR", "VOCAB_REAL_DATA_DIR",
                                                  "VOCAB_DATABASE_PATH", "VOCAB_ENABLE_OCR")})
        sys.path.insert(0, str(BACKEND))
        from fastapi.testclient import TestClient

        from app.cli import create_user_account
        from app.db import get_engine, get_session_factory
        from app.main import app

        require("application engine targets wrong database",
                Path(get_engine().url.database).resolve() == CLONE.resolve(), failures)
        with get_session_factory()() as session:
            # Two synthetic accounts; production's existing account rows are untouched.
            require("failed to create acceptance account A",
                    create_user_account(session, "accept_a_20261003", "CloneOnlyPass_A_123!", role="user"), failures)
            require("failed to create acceptance account B",
                    create_user_account(session, "accept_b_20261003", "CloneOnlyPass_B_123!", role="user"), failures)

        def client(username: str, password: str) -> TestClient:
            instance = TestClient(app, headers={"Origin": "http://testserver"})
            response = instance.post("/api/auth/login", json={"username": username, "password": password})
            require("login failed: " + username, response.status_code == 200, failures)
            return instance

        a = client("accept_a_20261003", "CloneOnlyPass_A_123!")
        b = client("accept_b_20261003", "CloneOnlyPass_B_123!")
        checks = evidence["checks"]
        baseline_lists = a.get("/api/lexicons").json()
        checks["netem_system_count"] = sum(x["owner_user_id"] is None and
            ("netem" in x["name"].lower() or x["source_type"] == "netem") for x in baseline_lists)
        selection_before = a.get("/api/lexicons/selection").json()
        checks["selection_without_netem"] = selection_before
        require("NETEM exists in production clone; absence case cannot be tested", checks["netem_system_count"] == 0, failures)
        require("no-NETEM default selected a lexicon", selection_before == {"lexicon_id": None, "source": "none"}, failures)

        def upload(filename: str, content: bytes):
            return {"file": (filename, BytesIO(content), "text/plain")}

        def import_list(owner: TestClient, title: str, filename: str, content: bytes):
            prior_count = len(owner.get("/api/lexicons").json())
            preview = owner.post("/api/lexicons/file-preview", files=upload(filename, content))
            require(title + " preview failed", preview.status_code == 200, failures)
            require(title + " preview wrote lexicon", len(owner.get("/api/lexicons").json()) == prior_count, failures)
            changed = owner.post("/api/lexicons/file-import", data={"name": title,
                "preview_sha256": preview.json()["sha256"]},
                files=upload(filename, content + b"\nextra"))
            require(title + " changed confirmation accepted", changed.status_code == 409, failures)
            confirmation = owner.post("/api/lexicons/file-import", data={"name": title,
                "preview_sha256": preview.json()["sha256"]}, files=upload(filename, content))
            require(title + " confirmation failed", confirmation.status_code == 201, failures)
            valid = [row["word"] for row in preview.json()["rows"] if row["status"] == "valid"]
            checks[title] = {"preview_counts": preview.json()["counts"],
                             "preview_valid_words": valid,
                             "imported_count": confirmation.json().get("imported_count"),
                             "lexicon_id": confirmation.json().get("id")}
            require(title + " preview/confirmation count differs",
                    confirmation.json().get("imported_count") == len(valid), failures)
            return confirmation.json()["id"], valid

        csv_id, csv_words = import_list(a, "验收CSV甲", "sample.csv",
            "word,meaning,part_of_speech\nBanana,香蕉,n.\nApple,苹果,n.\nbanana,重复,n.\n".encode())
        txt_id, txt_words = import_list(a, "验收TXT甲", "sample.txt",
            b"Apple\npear\napple\ninvalid phrase\n")
        b_id, b_words = import_list(b, "验收TXT乙", "sample.txt", b"Apple\nplum\n")
        with connect_ro(CLONE) as clone:
            entries = {}
            for lexicon_id in (csv_id, txt_id, b_id):
                entries[lexicon_id] = clone.execute(
                    "SELECT word, sequence, source_meanings FROM lexicon_entry "
                    "WHERE lexicon_id=? ORDER BY sequence", (lexicon_id,)).fetchall()
            checks["entries"] = {str(key): value for key, value in entries.items()}
            checks["private_metadata"] = clone.execute(
                "SELECT id, owner_user_id, visibility, source_type FROM lexicon "
                "WHERE id IN (?, ?, ?) ORDER BY id", (csv_id, txt_id, b_id)).fetchall()
        for lexicon_id, words in ((csv_id, csv_words), (txt_id, txt_words), (b_id, b_words)):
            require("entry order differs from preview: " + str(lexicon_id),
                    [row[0] for row in entries[lexicon_id]] == words and
                    [row[1] for row in entries[lexicon_id]] == list(range(1, len(words)+1)), failures)
        require("CSV user meanings changed", [json.loads(row[2]) for row in entries[csv_id]] ==
                [["香蕉"], ["苹果"]], failures)
        require("private lexicon metadata invalid", all(row[2:] == ("private", "user_file")
                for row in checks["private_metadata"]), failures)

        checks["isolation"] = {}
        for actor, foreign_ids in ((a, (b_id,)), (b, (csv_id, txt_id))):
            listed = {item["id"] for item in actor.get("/api/lexicons").json()}
            for foreign_id in foreign_ids:
                responses = [actor.get(f"/api/lexicons/{foreign_id}"),
                             actor.get("/api/study/today", params={"lexicon_id": foreign_id}),
                             actor.post(f"/api/lexicons/{foreign_id}/select")]
                statuses = [response.status_code for response in responses]
                checks["isolation"][str(foreign_id)] = {"listed": foreign_id in listed, "statuses": statuses}
                require("cross-account leak: " + str(foreign_id),
                        foreign_id not in listed and statuses == [404, 404, 404], failures)

        csv_queue = a.get("/api/study/today", params={"lexicon_id": csv_id}).json()["words"]
        txt_queue = a.get("/api/study/today", params={"lexicon_id": txt_id}).json()["words"]
        b_queue = b.get("/api/study/today", params={"lexicon_id": b_id}).json()["words"]
        checks["queues"] = {"csv": [(x["word"], x["meaning_origin"], x["status"]) for x in csv_queue],
                             "txt": [(x["word"], x["meaning_origin"], x["status"]) for x in txt_queue],
                             "b": [(x["word"], x["meaning_origin"], x["status"]) for x in b_queue]}
        require("CSV queue order/meaning marker incorrect",
                [x["word"] for x in csv_queue] == csv_words and
                all(x["meaning_origin"] == "user_provided" for x in csv_queue), failures)
        require("TXT queue order incorrect", [x["word"] for x in txt_queue] == txt_words, failures)
        require("same word shares state between lexicons/users", len({csv_queue[1]["word_state_id"],
            txt_queue[0]["word_state_id"], b_queue[0]["word_state_id"]}) == 3, failures)
        reviewed = a.post(f'/api/study/word-states/{csv_queue[1]["word_state_id"]}/review',
                          json={"result": "know"})
        require("review failed", reviewed.status_code == 200, failures)
        with connect_ro(CLONE) as clone:
            statuses = {state_id: clone.execute("SELECT status FROM user_word_state WHERE id=?",
                       (state_id,)).fetchone()[0] for state_id in (
                       csv_queue[1]["word_state_id"], txt_queue[0]["word_state_id"],
                       b_queue[0]["word_state_id"])}
        checks["progress_independence"] = {
            "csv_state_after": statuses[csv_queue[1]["word_state_id"]],
            "txt_state_after": statuses[txt_queue[0]["word_state_id"]],
            "other_user_state_after": statuses[b_queue[0]["word_state_id"]],
        }
        require("progress crossed a lexicon or account", checks["progress_independence"]["csv_state_after"] != "new" and
                checks["progress_independence"]["txt_state_after"] == "new" and
                checks["progress_independence"]["other_user_state_after"] == "new", failures)
        selected = a.post(f"/api/lexicons/{txt_id}/select")
        require("select failed", selected.status_code == 200, failures)
        checks["selection_before_restart"] = a.get("/api/lexicons/selection").json()
        a.close(); b.close()
        get_engine().dispose()
        # A new process provides a real application restart boundary.
        child = subprocess.run([sys.executable, str(__file__), "--restart-check", str(CLONE), str(txt_id)],
                               cwd=ROOT, env=env, text=True, capture_output=True, check=False,
                               encoding="utf-8", errors="replace")
        checks["restart"] = {"exit_code": child.returncode,
                             "stdout": child.stdout.strip(), "stderr_tail": child.stderr[-500:]}
        require("selection did not persist across process restart", child.returncode == 0, failures)
        with connect_ro(CLONE) as clone:
            final = state(clone)
        checks["final_integrity"] = final["integrity"]
        checks["final_foreign_key_errors"] = final["foreign_key_errors"]
        require("final clone integrity failed", final["integrity"] == "ok", failures)
        require("final clone foreign keys failed", final["foreign_key_errors"] == 0, failures)
        if ROLLBACK_PROBE.exists():
            raise RuntimeError(f"Refusing to overwrite rollback probe: {ROLLBACK_PROBE}")
        with connect_ro(CLONE) as source, sqlite3.connect(ROLLBACK_PROBE) as probe:
            source.backup(probe)
        with connect_ro(ROLLBACK_PROBE) as probe:
            probe_before = state(probe)
        downgrade_env = dict(env)
        # The physical staging path remains disposable while production stays
        # protected by the guard's built-in project data root.
        downgrade_env["VOCAB_REAL_DATA_DIR"] = str(ROOT / "protected-real-data")
        downgrade = subprocess.run([sys.executable, "-m", "alembic", "-c",
            str(BACKEND / "alembic.ini"), "-x",
            f"db_url=sqlite:///{ROLLBACK_PROBE.as_posix()}", "downgrade",
            "0013_concise_meaning_wikitext_binding"], cwd=BACKEND, env=downgrade_env,
            text=True, capture_output=True, check=False, encoding="utf-8", errors="replace")
        with connect_ro(ROLLBACK_PROBE) as probe:
            probe_after = state(probe)
        checks["rollback_boundary"] = {"target": "0013_concise_meaning_wikitext_binding",
            "exit_code": downgrade.returncode, "refusal_message_seen":
            "explicit lexicon selections would be lost" in downgrade.stdout + downgrade.stderr,
            "output_tail": (downgrade.stdout + downgrade.stderr)[-900:],
            "clone_unchanged": probe_after == probe_before,
            "integrity": probe_after["integrity"], "foreign_key_errors": probe_after["foreign_key_errors"]}
        require("0014 downgrade boundary failed", downgrade.returncode != 0 and
                checks["rollback_boundary"]["refusal_message_seen"] and
                probe_after == probe_before, failures)
    with connect_ro(SOURCE) as source:
        production_after = state(source)
    evidence["production_after"] = {"revision": production_after["revision"],
        "integrity": production_after["integrity"],
        "foreign_key_errors": production_after["foreign_key_errors"],
        "logical_unchanged": production_after == before,
        "source_file_sha256": {path.name: file_sha(path) for path in (
            SOURCE, SOURCE.with_name(SOURCE.name + "-wal")) if path.exists()}}
    require("production logical state changed during acceptance", production_after == before, failures)
    evidence["failures"] = failures
    evidence["verdict"] = "PASS" if not failures else "FAIL"
    REPORT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"verdict": evidence["verdict"], "failures": failures,
                      "report": str(REPORT), "clone": str(CLONE)}, ensure_ascii=False))
    return 0 if not failures else 1


def restart_check() -> int:
    clone = Path(sys.argv[2]).resolve()
    expected = int(sys.argv[3])
    os.environ.update({"VOCAB_DATA_DIR": str(clone.parent), "VOCAB_REAL_DATA_DIR": str(ROOT / "data"),
                       "VOCAB_DATABASE_PATH": str(clone), "VOCAB_ENABLE_OCR": "false"})
    sys.path.insert(0, str(BACKEND))
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        login = client.post("/api/auth/login", json={"username": "accept_a_20261003",
                                                     "password": "CloneOnlyPass_A_123!"})
        if login.status_code != 200:
            print("login status", login.status_code)
            return 1
        selection = client.get("/api/lexicons/selection").json()
        queue = client.get("/api/study/today").json()
        print(json.dumps({"selection": selection, "queue_lexicon_id": queue["lexicon_id"],
                          "words": [word["word"] for word in queue["words"]]}, ensure_ascii=False))
        return 0 if selection == {"lexicon_id": expected, "source": "explicit"} and queue["lexicon_id"] == expected else 1


if __name__ == "__main__":
    raise SystemExit(restart_check() if len(sys.argv) > 1 and sys.argv[1] == "--restart-check" else main())
