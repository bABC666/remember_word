"""Baseline snapshots and verified-database comparison.

A backup or database is only *verified* when it is compared against a recorded
**baseline snapshot** rather than a hard-coded row count from one moment in
time. Real databases legitimately grow while the application runs, so the
verifier classifies every table:

``IMMUTABLE``
    Existing rows must survive byte-for-byte. Rows may be *added* (a live
    database keeps accepting new imports, articles and words), but no row that
    the baseline recorded may change or disappear.

``APPEND_ONLY``
    Same as immutable, and additionally the intent is that history is never
    rewritten. Treated identically by the verifier; the label documents intent.

The distinction that actually matters is *loss*: a row present in the baseline
must still be present with an identical value hash, whatever the table.

This module never writes to the databases it inspects: every connection is
opened read-only.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

#: Tables whose recorded rows may never change or disappear.
IMMUTABLE_TABLES: tuple[str, ...] = (
    "word",
    "article",
    "article_word_exposure",
    "import_batch",
    "import_image",
    "import_candidate",
    # V1.2 content tables: lexicon content is shared and must not drift.
    "lexicon",
    "lexicon_entry",
)

#: Tables that accumulate history. Same enforcement, different intent.
APPEND_ONLY_TABLES: tuple[str, ...] = (
    "review_event",
    "history_event",
    "article_word_lookup",
    # V1.2 per-user tables.
    "user",
    "user_session",
    "user_settings",
    "user_lexicon",
    "user_word_state",
)

CLASSIFICATIONS: dict[str, str] = {
    **{name: "immutable" for name in IMMUTABLE_TABLES},
    **{name: "append_only" for name in APPEND_ONLY_TABLES},
}

#: Tables the verifier ignores entirely (engine bookkeeping).
IGNORED_TABLES: frozenset[str] = frozenset({"alembic_version", "sqlite_sequence"})

#: Columns excluded from row identities.
#:
#: A migration may legitimately ADD a column to an existing table (the V1.2
#: migration adds ``user_id`` and ``lexicon_entry_id`` bridges). Hashing
#: ``select *`` made every existing row look "modified" for that reason alone.
#: Row identity is therefore the recorded business columns, and these bookkeeping
#: columns are ignored. Anything not listed is still protected: a real change to
#: a content or learning column is caught.
IGNORED_COLUMNS: dict[str, frozenset[str]] = {
    "word": frozenset({"user_id", "lexicon_entry_id"}),
    "article": frozenset({"user_id"}),
    "review_event": frozenset({"user_id"}),
    "import_batch": frozenset({"user_id"}),
    "import_candidate": frozenset({"lexicon_entry_id"}),
    "history_event": frozenset({"user_id"}),
}

BASELINE_VERSION = 1


def classify(table: str) -> str:
    return CLASSIFICATIONS.get(table, "append_only")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connect_readonly(database: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)


def list_tables(connection: sqlite3.Connection) -> list[str]:
    return sorted(
        row[0]
        for row in connection.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
        )
        if row[0] not in IGNORED_TABLES
    )


def row_hash(values: tuple) -> str:
    """Deterministic hash of one row, independent of column order in the query."""
    normalized = []
    for value in values:
        if isinstance(value, bytes):
            normalized.append(("b", value.hex()))
        else:
            normalized.append(("v", value))
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def identity_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    """Columns that define a row's identity for verification purposes."""
    ignored = IGNORED_COLUMNS.get(table, frozenset())
    columns = [
        row[1] for row in connection.execute(f'pragma table_info("{table}")') if row[1]
    ]
    kept = [name for name in columns if name not in ignored]
    return kept or columns


def table_fingerprint(
    connection: sqlite3.Connection,
    table: str,
    *,
    hash_rows: bool = True,
    columns: list[str] | None = None,
) -> dict[str, object]:
    """Row count plus a per-row hash map keyed by rowid."""
    count = connection.execute(f'select count(*) from "{table}"').fetchone()[0]
    info: dict[str, object] = {"rows": count}
    if not hash_rows:
        return info
    selected = columns if columns is not None else identity_columns(connection, table)
    projected = ", ".join(f'"{name}"' for name in selected)
    hashes: dict[str, str] = {}
    try:
        for rowid, *values in connection.execute(
            f'select rowid, {projected} from "{table}"'
        ):
            hashes[str(rowid)] = row_hash(tuple(values))
    except sqlite3.Error:
        # WITHOUT ROWID tables fall back to positional keys.
        for index, row in enumerate(connection.execute(f'select {projected} from "{table}"')):
            hashes[str(index)] = row_hash(tuple(row))
    info["row_hashes"] = hashes
    info["identity_columns"] = selected
    return info


def revision(connection: sqlite3.Connection) -> str | None:
    try:
        row = connection.execute("select version_num from alembic_version").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def capture_baseline(
    database: Path,
    *,
    label: str = "",
    hash_rows: bool = True,
    notes: str = "",
) -> dict[str, object]:
    """Record a verified snapshot of a database that is currently trusted."""
    if not database.exists():
        raise FileNotFoundError(database)
    connection = connect_readonly(database)
    try:
        integrity = connection.execute("pragma integrity_check").fetchone()[0]
        foreign_key_violations = len(connection.execute("pragma foreign_key_check").fetchall())
        tables = list_tables(connection)
        snapshot: dict[str, object] = {
            "baseline_version": BASELINE_VERSION,
            "label": label,
            "notes": notes,
            "captured_at_utc": datetime.now(UTC).isoformat(),
            "database": str(database),
            "bytes": database.stat().st_size,
            "sha256": sha256_file(database),
            "alembic_revision": revision(connection),
            "integrity_check": integrity,
            "foreign_key_check_violations": foreign_key_violations,
            "tables": {
                name: {
                    "classification": classify(name),
                    **table_fingerprint(connection, name, hash_rows=hash_rows),
                }
                for name in tables
            },
        }
        return snapshot
    finally:
        connection.close()


def save_baseline(snapshot: dict[str, object], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_baseline(path: Path) -> dict[str, object]:
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    if snapshot.get("baseline_version") != BASELINE_VERSION:
        raise ValueError(
            f"baseline version {snapshot.get('baseline_version')!r} is not supported "
            f"(expected {BASELINE_VERSION})"
        )
    return snapshot


def compare_against_baseline(
    database: Path,
    baseline: dict[str, object],
    *,
    require_revision: bool = True,
    expected_revision: str | None = None,
) -> tuple[bool, dict[str, object]]:
    """Compare a database to a baseline snapshot.

    Fails on: lost tables, lost rows, changed row values, integrity problems and
    foreign-key violations. Legitimate growth (new rows, new tables) is allowed
    and reported.
    """
    report: dict[str, object] = {
        "database": str(database),
        "baseline_label": baseline.get("label"),
        "baseline_sha256": baseline.get("sha256"),
        "failures": [],
        "growth": [],
    }
    failures: list[str] = report["failures"]  # type: ignore[assignment]
    growth: list[str] = report["growth"]  # type: ignore[assignment]

    if not database.exists():
        failures.append("database file does not exist")
        report["verified"] = False
        return False, report

    report["bytes"] = database.stat().st_size
    report["sha256"] = sha256_file(database)

    connection = connect_readonly(database)
    try:
        integrity = connection.execute("pragma integrity_check").fetchone()[0]
        report["integrity_check"] = integrity
        if integrity != "ok":
            failures.append(f"integrity_check = {integrity}")

        foreign_keys = connection.execute("pragma foreign_key_check").fetchall()
        report["foreign_key_check_violations"] = len(foreign_keys)
        if foreign_keys:
            failures.append(f"foreign_key_check reported {len(foreign_keys)} violations")

        current_revision = revision(connection)
        report["alembic_revision"] = current_revision
        if require_revision:
            wanted = expected_revision or baseline.get("alembic_revision")
            if wanted is not None and current_revision != wanted:
                failures.append(f"alembic revision {current_revision!r} != expected {wanted!r}")

        present = set(list_tables(connection))
        baseline_tables: dict[str, object] = baseline.get("tables", {})  # type: ignore[assignment]

        missing_tables = sorted(set(baseline_tables) - present)
        if missing_tables:
            failures.append(f"tables missing from the database: {missing_tables}")

        new_tables = sorted(present - set(baseline_tables))
        if new_tables:
            growth.append(f"new tables: {new_tables}")

        per_table: dict[str, object] = {}
        for table, recorded in sorted(baseline_tables.items()):
            recorded = dict(recorded)  # type: ignore[arg-type]
            classification = str(recorded.get("classification", classify(table)))
            entry: dict[str, object] = {"classification": classification}
            if table not in present:
                entry["status"] = "missing"
                per_table[table] = entry
                continue

            current = table_fingerprint(
                connection,
                table,
                columns=recorded.get("identity_columns") or None,
            )
            entry["rows_baseline"] = recorded.get("rows")
            entry["rows_current"] = current["rows"]
            entry["status"] = "ok"

            if int(current["rows"]) < int(recorded.get("rows", 0)):
                failures.append(
                    f"{table}: row count fell from {recorded.get('rows')} to {current['rows']}"
                )
                entry["status"] = "rows_lost"

            recorded_hashes: dict[str, str] = recorded.get("row_hashes", {})  # type: ignore[assignment]
            current_hashes: dict[str, str] = current.get("row_hashes", {})  # type: ignore[assignment]
            if recorded_hashes:
                lost = sorted(set(recorded_hashes) - set(current_hashes))
                changed = sorted(
                    key
                    for key in set(recorded_hashes) & set(current_hashes)
                    if recorded_hashes[key] != current_hashes[key]
                )
                if lost:
                    failures.append(f"{table}: {len(lost)} baseline rows are gone: {lost[:10]}")
                    entry["status"] = "rows_lost"
                if changed:
                    failures.append(
                        f"{table}: {len(changed)} baseline rows changed: {changed[:10]}"
                    )
                    entry["status"] = "rows_changed"
                added = len(set(current_hashes) - set(recorded_hashes))
                if added:
                    growth.append(f"{table}: +{added} rows")
            per_table[table] = entry

        report["tables"] = per_table
    finally:
        connection.close()

    report["verified"] = not failures
    return not failures, report
