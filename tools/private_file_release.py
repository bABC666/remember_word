"""Read-only release fingerprints and verified SQLite online backup.

Only ``backup`` creates a new destination file. Never points a write at source.
Reports contain column names, counts and hashes, not user rows.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sqlite3
from pathlib import Path


def connect_ro(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ValueError(f"database is missing: {path}")
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def encoded(value: object) -> object:
    if isinstance(value, bytes):
        return {"blob_base64": base64.b64encode(value).decode("ascii")}
    return value


def fingerprint(db: sqlite3.Connection, columns_by_table: dict[str, list[str]] | None = None) -> dict:
    tables = [row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version' ORDER BY name"
    )]
    if columns_by_table is None:
        columns_by_table = {
            table: [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
            for table in tables
        }
    result = {}
    for table, columns in columns_by_table.items():
        if table not in tables:
            raise ValueError(f"missing original table: {table}")
        available = [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
        if any(column not in available for column in columns):
            raise ValueError(f"missing original column in {table}")
        quoted = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
        digest = hashlib.sha256()
        count = 0
        for row in db.execute(f'SELECT {quoted} FROM "{table}" ORDER BY rowid'):
            digest.update(json.dumps([encoded(value) for value in row],
                                     ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            digest.update(b"\n")
            count += 1
        result[table] = {"columns": columns, "count": count, "sha256": digest.hexdigest()}
    versions = db.execute("SELECT version_num FROM alembic_version").fetchall()
    return {
        "revision": versions[0][0] if len(versions) == 1 else None,
        "integrity": db.execute("PRAGMA integrity_check").fetchone()[0],
        "foreign_key_errors": len(db.execute("PRAGMA foreign_key_check").fetchall()),
        "tables": result,
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def healthy(state: dict, revision: str) -> None:
    if (state["revision"], state["integrity"], state["foreign_key_errors"]) != (revision, "ok", 0):
        raise ValueError(f"revision/integrity/FK gate failed: {state['revision']}, "
                         f"{state['integrity']}, {state['foreign_key_errors']}")


def write_new(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "backup", "compare"):
        command = sub.add_parser(name)
        command.add_argument("--database", type=Path, required=True)
        if name == "backup":
            command.add_argument("--output", type=Path, required=True)
        elif name == "snapshot":
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--revision", required=True)
        else:
            command.add_argument("--baseline", type=Path, required=True)
            command.add_argument("--revision", required=True)
    args = parser.parse_args()
    if args.command == "snapshot":
        with connect_ro(args.database) as source:
            state = fingerprint(source)
        healthy(state, args.revision)
        write_new(args.output, state)
        print(f"SNAPSHOT PASS: {args.output}")
    elif args.command == "backup":
        if args.output.exists():
            raise ValueError(f"refusing to overwrite backup: {args.output}")
        with connect_ro(args.database) as source:
            before = fingerprint(source)
            healthy(before, "0007_bridge_foreign_keys")
            # Exclusive creation prevents replacing a previous release backup.
            with args.output.open("xb"):
                pass
            with sqlite3.connect(args.output) as destination:
                source.backup(destination)
            after = fingerprint(source)
        with connect_ro(args.output) as destination:
            copied = fingerprint(destination)
        if before != after or after != copied:
            raise ValueError("source changed during backup or backup differs from source")
        print(json.dumps({"backup": str(args.output), "bytes": args.output.stat().st_size,
                          "sha256": sha256(args.output), "revision": copied["revision"],
                          "logical_match": True}, ensure_ascii=False))
    else:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        healthy(baseline, "0007_bridge_foreign_keys")
        with connect_ro(args.database) as source:
            current = fingerprint(source, {
                name: data["columns"] for name, data in baseline["tables"].items()
            })
        healthy(current, args.revision)
        drift = [name for name, data in baseline["tables"].items()
                 if current["tables"][name] != data]
        if drift:
            raise ValueError(f"original row drift: {drift}")
        print(f"COMPARE PASS: {len(baseline['tables'])} original business tables unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
