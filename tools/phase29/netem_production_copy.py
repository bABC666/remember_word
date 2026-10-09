"""Production online-backup rehearsal. All writes are confined to fresh copies."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import socket
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from netem_release import (
    CANDIDATE_SHA,
    FROZEN,
    PRODUCTION,
    ROOT,
    atomic_confirm,
    build_final_plan,
    engine_for,
    guard_database,
    readonly,
    sha,
    verify_package,
    verify_revision,
    write_json,
)
from sqlalchemy import event, select
from sqlalchemy.orm import Session

DEFAULT = ROOT / "test-artifacts/netem-production-copy-20261005"
ALLOWED_IMPORT = {"lexicon", "lexicon_entry", "source_artifact", "public_import_run",
                  "public_import_run_source", "entry_source_evidence"}


def safe_root(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to((ROOT / "test-artifacts").resolve()) or resolved == (ROOT / "test-artifacts").resolve():
        raise ValueError("rehearsal requires a child of test-artifacts")
    return resolved


def snapshot(database: Path) -> dict:
    result = {}
    with readonly(database) as con:
        tables = [r[0] for r in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
        for table in tables:
            info = con.execute(f'pragma table_info("{table}")').fetchall()
            keys = [i for _, i in sorted((r[5], i) for i, r in enumerate(info) if r[5])]
            assert keys, table
            values = {}
            for row in con.execute(f'select * from "{table}"'):
                key = json.dumps([row[i] for i in keys], separators=(",", ":"))
                values[key] = hashlib.sha256(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            result[table] = values
    return result


def original_rows_unchanged(before: dict, after: dict) -> dict:
    for table, rows in before.items():
        assert all(after[table].get(key) == value for key, value in rows.items()), table
    return {table: len(rows) for table, rows in before.items()}


def production_files() -> dict:
    return {p.name: {"sha256": sha(p), "mtime_ns": p.stat().st_mtime_ns, "bytes": p.stat().st_size}
            for p in [PRODUCTION, Path(str(PRODUCTION) + "-wal"), Path(str(PRODUCTION) + "-shm")] if p.exists()}


def port_8000() -> bool:
    with socket.socket() as sock:
        sock.settimeout(.3)
        return sock.connect_ex(("127.0.0.1", 8000)) == 0


def online_backup(source: Path, target: Path) -> None:
    if target.exists() or not target.resolve().is_relative_to((ROOT / "test-artifacts").resolve()):
        raise ValueError("backup target must be new and isolated")
    target.parent.mkdir(parents=True, exist_ok=True)
    with readonly(source) as src, closing(sqlite3.connect(target)) as dest:
        src.backup(dest)
        dest.execute("pragma journal_mode=delete")
    verify_revision(target)


def prepare(root: Path) -> dict:
    verify_package()
    verify_revision(PRODUCTION)
    if root.exists():
        raise ValueError("new rehearsal directory required; do not overwrite evidence")
    before = production_files()
    listening = port_8000()
    root.mkdir(parents=True)
    backup = root / "protected-before.db"
    online_backup(PRODUCTION, backup)
    backup.chmod(stat.S_IREAD)
    online_backup(backup, root / "working/vocab.db")
    baseline = snapshot(backup)
    assert snapshot(root / "working/vocab.db") == baseline
    write_json(root / "original-rows.json", baseline)
    original_docs = json.loads((ROOT / "docs/NETEM-FINAL-CANDIDATE-EVIDENCE-2026-10-05.json").read_text("utf-8"))["preservation"]["preserved_documents"]
    assert all(sha(ROOT / p) == value for p, value in original_docs.items())
    receipt = {"production_database": str(PRODUCTION), "revision": "0015_session_autoincrement",
        "online_backup": str(backup), "backup_sha256": sha(backup), "backup_readonly": bool(backup.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY) if os.name == "nt" else not bool(backup.stat().st_mode & stat.S_IWUSR),
        "candidate_sha256": CANDIDATE_SHA, "production_files_before": before,
        "production_port_8000_listening_before": listening, "original_counts": {k: len(v) for k, v in baseline.items()},
        "preserved_documents": original_docs, "created_at_utc": datetime.now(UTC).isoformat()}
    write_json(root / "backup-receipt.json", receipt)
    return receipt


def fixtures(root: Path) -> None:
    from app.api.deps import ensure_user_settings
    from app.models import Lexicon, LexiconEntry, User, UserLexicon, UserWordState
    from app.security import hash_password
    database = guard_database(root / "working/vocab.db")
    password = os.environ.get("NETEM_COPY_PASSWORD", "")
    if len(password) < 12:
        raise ValueError("set a copy-only NETEM_COPY_PASSWORD (never written to receipts)")
    engine = engine_for(database)
    with Session(engine) as session:
        assert session.scalar(select(User).where(User.username == "netem_copy_admin")) is None
        users = [User(username=name, display_name="副本验收", role=role, password_hash=hash_password(password))
                 for name, role in [("netem_copy_admin", "admin"), ("netem_copy_private", "user"), ("netem_copy_default", "user")]]
        session.add_all(users); session.flush()
        for user in users:
            ensure_user_settings(session, user).onboarding_seen = True
        private = Lexicon(name="NETEM", owner_user_id=users[1].id, visibility="private", source_type="user_file")
        session.add(private); session.flush()
        session.add(UserLexicon(user_id=users[1].id, lexicon_id=private.id, enabled=True))
        for i, word in enumerate(["the", "set", "modern"], 1):
            entry = LexiconEntry(lexicon_id=private.id, word=word, normalized_word=word,
                sequence=i, source_meanings=["副本自导同词释义"], source_raw="")
            session.add(entry); session.flush()
            session.add(UserWordState(user_id=users[1].id, lexicon_entry_id=entry.id,
                status="reviewing", recall_success=2, next_review_at=datetime.now(UTC) - timedelta(days=1)))
        ensure_user_settings(session, users[1]).selected_lexicon_id = private.id
        session.commit()
    engine.dispose()
    write_json(root / "before-import-rows.json", snapshot(database))


def apply(database: Path, plan: dict) -> dict:
    from app.models import User
    database = guard_database(database)
    verify_revision(database)
    engine = engine_for(database)
    try:
        with Session(engine) as session:
            admin = session.scalar(select(User).where(User.username == "netem_copy_admin"))
            return atomic_confirm(session, plan=plan, administrator=admin)
    finally:
        engine.dispose()


def import_copy(root: Path) -> dict:
    database = guard_database(root / "working/vocab.db")
    if (root / "import-receipt.json").exists():
        before_repeat = snapshot(database)
        result = apply(database, build_final_plan())
        assert result["status"] == "already_applied" and snapshot(database) == before_repeat
        return result
    if not (root / "before-import-rows.json").exists():
        fixtures(root)
    before = json.loads((root / "before-import-rows.json").read_text("utf-8"))
    plan = build_final_plan()
    if not (root / "locked-plan.json").exists():
        write_json(root / "locked-plan.json", plan)
    result = apply(database, plan)
    after = snapshot(database)
    original_rows_unchanged(before, after)
    delta = {table: len(after[table]) - len(rows) for table, rows in before.items()}
    assert all(value == 0 for table, value in delta.items() if table not in ALLOWED_IMPORT)
    assert delta == {table: (5528 if table == "lexicon_entry" else 16430 if table == "entry_source_evidence" else 3 if table in {"source_artifact", "public_import_run_source"} else 1 if table in {"lexicon", "public_import_run"} else 0) for table in before}
    write_json(root / "import-receipt.json", {"result": result, "delta": delta, "all_preexisting_rows_unchanged": True})
    retry = apply(database, plan)
    assert retry["status"] == "already_applied" and snapshot(database) == after
    write_json(root / "repeat-receipt.json", {"status": retry["status"], "all_table_rows_identical": True})
    return result


def full_verify(root: Path) -> dict:
    from verify_final_netem_candidate import verify

    from app.models import User, UserSettings
    from app.services.lexicon_selection import effective_lexicon_selection
    database = guard_database(root / "working/vocab.db")
    result = verify(FROZEN, database)
    with readonly(database) as con:
        evidence = con.execute("select a.name,e.normalized_word,e.row_locator,e.field_kind,e.sense_key,e.raw_text,e.source_revision from entry_source_evidence e join source_artifact a on a.id=e.source_artifact_id join lexicon_entry l on l.id=e.lexicon_entry_id join lexicon x on x.id=l.lexicon_id where x.source_type='netem' and x.owner_user_id is null").fetchall()
        expected = {}
        for name in ["netem-words.csv", "wikdict.csv", "zhwiktionary.csv"]:
            with (FROZEN / "public-package" / name).open(encoding="utf-8", newline="") as file:
                for line, row in enumerate(csv.DictReader(file), 2):
                    expected[name, row["word"].casefold(), line] = row
        for name, word, line, kind, position, raw, revision in evidence:
            row = expected[name, word, line]
            assert kind in {"word", "meaning"} and raw == row[kind]
            assert position == row["source_position"] and revision == row["source_revision"]
        overlap_ids = [r[0] for r in con.execute("select a.id from lexicon_entry a join lexicon x on x.id=a.lexicon_id where x.owner_user_id is not null and exists(select 1 from lexicon_entry b join lexicon y on y.id=b.lexicon_id where y.source_type='netem' and y.owner_user_id is null and a.normalized_word=b.normalized_word)")]
        counts = dict(con.execute("select a.name,count(*) from entry_source_evidence e join source_artifact a on a.id=e.source_artifact_id where e.field_kind='meaning' group by a.name"))
        assert counts == {"wikdict.csv": 4814, "zhwiktionary.csv": 637}
    original = json.loads((root / "original-rows.json").read_text("utf-8"))
    private_overlap = sum(json.dumps([entry_id], separators=(",", ":")) in original["lexicon_entry"] for entry_id in overlap_ids)
    preserved = original_rows_unchanged(original, snapshot(database))
    selections = {"explicit_original_preserved": 0, "unselected_original_recommended": 0}
    engine = engine_for(database)
    with Session(engine) as session:
        for key in original["user"]:
            user = session.get(User, json.loads(key)[0])
            settings = session.get(UserSettings, user.id)
            selected, source = effective_lexicon_selection(session, user)
            if settings and settings.selected_lexicon_id is not None:
                assert (selected, source) == (settings.selected_lexicon_id, "explicit")
                selections["explicit_original_preserved"] += 1
            else:
                assert source == "recommended"
                selections["unselected_original_recommended"] += 1
        tester = session.scalar(select(User).where(User.username == "netem_copy_default"))
        assert effective_lexicon_selection(session, tester)[1] == "recommended"
    engine.dispose()
    result.update(all_original_rows_unchanged=True, original_counts=preserved,
                  dictionary_counts=counts,
                  same_word_private_entries_preserved=private_overlap, all_evidence_rows_verified=len(evidence), selections=selections,
                  code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip())
    write_json(root / "content-verification.json", result)
    return result


def fault_child(database: Path, mode: str) -> None:
    from app.models import User
    database = guard_database(database)
    engine = engine_for(database)
    count = 0

    @event.listens_for(engine, "before_cursor_execute")
    def inject(_conn, _cursor, statement, _parameters, _context, _many):
        nonlocal count
        if "INSERT INTO lexicon_entry" in statement:
            count += 1
            if count == 20:
                if mode == "kill":
                    os._exit(86)
                raise RuntimeError("copy-only precommit injected failure")
    with Session(engine) as session:
        admin = session.scalar(select(User).where(User.username == "netem_copy_admin"))
        atomic_confirm(session, plan=build_final_plan(), administrator=admin)


def failures(root: Path) -> dict:
    # Reconstruct the seeded pre-import state from a protected production backup.
    attempt = root / ("faults-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
    attempt.mkdir()
    scratch = attempt / "failure-base"
    scratch.mkdir()
    online_backup(root / "protected-before.db", scratch / "working/vocab.db")
    fixtures(scratch)
    records = {}
    for mode in ["exception", "kill"]:
        target = attempt / f"failure-{mode}.db"
        online_backup(scratch / "working/vocab.db", target)
        before = snapshot(target)
        child = subprocess.run([sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "fault-child", "--database", str(target), "--mode", mode], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
        assert child.returncode != 0 and (mode != "kill" or child.returncode == 86)
        if mode == "exception":
            assert "copy-only precommit injected failure" in child.stderr
        # Opening this isolated copy writable lets SQLite recover a hot journal.
        with closing(sqlite3.connect(target)) as con:
            con.execute("select count(*) from lexicon").fetchone()
        assert snapshot(target) == before, mode
        applied = apply(target, build_final_plan())
        assert applied["entries_created"] == 5528
        after = snapshot(target)
        retry = apply(target, build_final_plan())
        assert retry["status"] == "already_applied" and snapshot(target) == after
        records[mode] = {"database": str(target), "exit_code": child.returncode, "precommit_all_rows_rolled_back": True,
            "no_empty_public_NETEM": True, "retry_entries_created": 5528, "repeat_no_changes": True}
    write_json(root / "failure-receipt.json", records)
    return records


def browser_fixtures(root: Path) -> None:
    from app.models import Lexicon, LexiconEntry, User, UserWordState
    from app.services.userdata import get_or_create_word_state
    engine = engine_for(guard_database(root / "working/vocab.db"))
    with Session(engine) as session:
        user = session.scalar(select(User).where(User.username == "netem_copy_default"))
        library = session.scalar(select(Lexicon).where(Lexicon.source_type == "netem", Lexicon.owner_user_id.is_(None)))
        for entry in session.scalars(select(LexiconEntry).where(LexiconEntry.lexicon_id == library.id)):
            state = get_or_create_word_state(session, user, entry)
            if entry.word == "modern":
                state.status = "reviewing"
                state.next_review_at = datetime.now(UTC) - timedelta(days=1)
        session.commit()
        assert session.scalar(select(UserWordState).where(UserWordState.user_id == user.id)) is not None
    engine.dispose()


def serve(root: Path) -> None:
    import uvicorn
    database = guard_database(root / "working/vocab.db")
    verify_revision(database)
    os.environ["VOCAB_DATABASE_PATH"] = str(database)
    os.environ["VOCAB_DATA_DIR"] = str(database.parent)
    os.environ["VOCAB_ENABLE_OCR"] = "false"
    from app.config import get_settings
    get_settings.cache_clear()
    config = get_settings()
    assert config.database_path == database
    assert all(p.resolve().is_relative_to(root) for p in [config.data_dir, config.uploads_dir, config.backups_dir, config.config_dir])
    uvicorn.run("app.main:app", host="127.0.0.1", port=8772)


def withdrawal(root: Path) -> dict:
    from app.models import Lexicon, User
    from app.services.public_lexicon_confirm import ConfirmRefused
    from app.services.study import build_today_queue
    target = root / ("withdrawal-" + datetime.now(UTC).strftime("%H%M%S%f") + ".db")
    online_backup(guard_database(root / "working/vocab.db"), target)
    before = snapshot(target)
    with closing(sqlite3.connect(target)) as con:
        library_id = con.execute("select id from lexicon where source_type='netem' and owner_user_id is null and visibility='public'").fetchone()[0]
        assert con.execute("update lexicon set visibility='private' where id=?", (library_id,)).rowcount == 1
        con.commit()
    after = snapshot(target)
    assert {table for table in before if before[table] != after[table]} == {"lexicon"}
    engine = engine_for(target)
    with Session(engine) as session:
        for user in session.scalars(select(User).where(User.username.in_(["netem_copy_private", "netem_copy_default"]))):
            assert all(view.entry.lexicon_id != library_id for view in build_today_queue(session, user, limit=200).words)
        assert session.get(Lexicon, library_id).visibility == "private"
        try:
            apply(target, build_final_plan())
        except ConfirmRefused as error:
            assert "ambiguous or unrelated" in str(error)
        else:
            raise AssertionError("withdrawn target must refuse repeat import")
    engine.dispose()
    assert snapshot(target) == after
    result = {"database": str(target), "changed_tables": ["lexicon"], "all_user_data_preserved": True,
              "fallback_queue_excludes_withdrawn_library": True, "reimport_refused": True}
    write_json(root / "withdrawal-receipt.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "fixtures", "browser-fixtures", "serve", "withdrawal", "import", "verify", "failures", "fault-child"])
    parser.add_argument("--output", type=Path, default=DEFAULT)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--mode", choices=["exception", "kill"])
    args = parser.parse_args()
    root = safe_root(args.output)
    if args.action == "fault-child":
        fault_child(args.database, args.mode)
        return
    result = {"prepare": prepare, "fixtures": fixtures, "browser-fixtures": browser_fixtures, "serve": serve,
              "withdrawal": withdrawal,
              "import": import_copy, "verify": full_verify, "failures": failures}[args.action](root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
