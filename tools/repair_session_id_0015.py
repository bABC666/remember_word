"""Repair the reviewed 0014 session collision; refuse every other situation.

Run only with a quiesced target and a freshly verified online backup. The same
command must pass on a new copy of that backup before it is used on production.
No secrets or session fingerprints are emitted. All database predicates and
postconditions run under one BEGIN IMMEDIATE transaction, including reads of
the two read-only attached evidence databases. An exception rolls back.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def rows(db: sqlite3.Connection, schema: str, table: str) -> list:
    quoted = table.replace('"', '""')
    return db.execute(f'SELECT * FROM {schema}."{quoted}" ORDER BY rowid').fetchall()


def contents(db: sqlite3.Connection, schema: str) -> dict:
    return {name: rows(db, schema, name) for (name,) in db.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )}


def repair(database: Path, backup: Path, historical: Path, baseline: Path,
           backup_sha256: str, *, apply: bool = False) -> dict:
    paths = [p.resolve(strict=True) for p in (database, backup, historical, baseline)]
    require(len(set(paths)) == 4, "target and evidence paths must be distinct")
    baseline_data = json.loads(baseline.read_text(encoding="utf-8"))
    require(baseline_data["alembic_revision"] == "0007_bridge_foreign_keys",
            "historical baseline must be 0007")
    require(file_hash(historical) == baseline_data["sha256"], "historical backup hash mismatch")
    require(file_hash(backup) == backup_sha256.lower(), "current backup hash mismatch")
    db = sqlite3.connect(database.resolve().as_uri() + "?mode=rw", uri=True, timeout=1)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        for schema, path in (("evidence", backup), ("historical", historical)):
            db.execute(f"ATTACH DATABASE ? AS {schema}",
                       (path.resolve().as_uri() + "?mode=ro",))
        db.execute("BEGIN IMMEDIATE")
        before = contents(db, "main")
        require(before == contents(db, "evidence"), "target differs from current backup")
        schema = db.execute("SELECT type,name,tbl_name,sql FROM main.sqlite_master "
                            "ORDER BY type,name").fetchall()
        require(schema == db.execute("SELECT type,name,tbl_name,sql FROM evidence.sqlite_master "
                                     "ORDER BY type,name").fetchall(), "backup schema mismatch")
        for name in ("main", "evidence", "historical"):
            require(db.execute(f"PRAGMA {name}.integrity_check").fetchall() == [("ok",)],
                    f"{name} integrity failure")
            require(not db.execute(f"PRAGMA {name}.foreign_key_check").fetchall(),
                    f"{name} foreign key failure")
        require(before["alembic_version"] == [("0014_selected_lexicon",)], "target must be 0014")
        require(rows(db, "historical", "alembic_version") == [("0007_bridge_foreign_keys",)],
                "historical revision mismatch")
        columns = [r[1] for r in db.execute("PRAGMA main.table_info(user_session)")]
        require(columns == ["id", "user_id", "token_hash", "created_at", "expires_at",
                            "last_seen_at", "revoked_at", "user_agent"], "session columns changed")
        current = before["user_session"]
        old = rows(db, "historical", "user_session")
        require([(r[0], r[1]) for r in current] == [(1, 2)], "current session set changed")
        require([(r[0], r[1]) for r in old] == [(1, 1)], "historical session set changed")
        require(current[0][2] != old[0][2], "session token identity did not change")
        require(db.execute("SELECT julianday(?) > julianday(?)",
                           (current[0][3], old[0][3])).fetchone() == (1,),
                "current session must be newer")
        require(db.execute("SELECT username FROM user WHERE id=2").fetchall()
                == [("release_smoke_20261003",)], "smoke account identity changed")
        require(db.execute("SELECT count(*) FROM history_event WHERE user_id=2 "
                           "AND event_type='user_login' AND julianday(timestamp)>=julianday(?)",
                           (current[0][3],)).fetchone()[0] == 1, "login evidence changed")
        require(not db.execute("SELECT 1 FROM user_session WHERE id=2").fetchall(), "ID 2 occupied")
        for table in before:
            quoted = table.replace('"', '""')
            require(not any(r[2] == "user_session" for r in
                            db.execute(f'PRAGMA main.foreign_key_list("{quoted}")')),
                    "foreign key references session table")
        require(not db.execute("SELECT 1 FROM history_event WHERE entity_type='user_session' "
                               "AND CAST(entity_id AS TEXT)='1'").fetchall(),
                "audit reference to session 1")
        require(not db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger'").fetchall(),
                "unexpected trigger")
        require(db.execute("UPDATE user_session SET id=2 WHERE id=1 AND user_id=2").rowcount == 1,
                "update affected unexpected number of rows")
        expected = dict(before)
        expected["user_session"] = [(2, *current[0][1:])]
        require(contents(db, "main") == expected, "data or session non-ID fields changed")
        require(db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                           "ORDER BY type,name").fetchall() == schema, "schema changed")
        require(db.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "post integrity failed")
        require(not db.execute("PRAGMA foreign_key_check").fetchall(), "post FK failed")
        if apply:
            db.commit()
        else:
            db.rollback()
        return {"passed": True, "committed": apply, "old_id": 1, "new_id": 2,
                "non_id_fields_unchanged": 7, "other_tables_unchanged": len(before) - 2}
    finally:
        if db.in_transaction:
            db.rollback()
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("database", "backup", "historical", "baseline"):
        parser.add_argument("--" + option, type=Path, required=True)
    parser.add_argument("--backup-sha256", required=True)
    parser.add_argument("--apply", action="store_true", help="commit; default rolls back a rehearsal")
    args = parser.parse_args()
    try:
        print(json.dumps(repair(**vars(args))))
        return 0
    except (ValueError, sqlite3.Error, OSError, KeyError) as error:
        # SQLite error strings cannot contain bound session values here.
        print(json.dumps({"passed": False, "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
