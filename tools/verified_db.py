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

Two narrow allowances keep that rule usable on a database that is in use without
turning any of it off (see ``IGNORED_COLUMNS`` and ``ROW_TOLERANT_TABLES``):

* **Runtime-mutable columns are not part of a row's identity.** A session's
  ``last_seen_at``, a user's ``updated_at``, a word's ``next_review_at`` and the
  counters the application derives from ``review_event`` all change by design
  while people use the product. Pinning them made the verifier cry wolf on
  ordinary use, which is how a gate ends up being ignored. What stays in the
  identity is what a rewrite would have to forge: primary keys, ownership, the
  ``lexicon_entry_id`` / ``legacy_word_id`` bridge, ``token_hash`` and
  ``first_seen``.

* **Rows in a row-tolerant table may legitimately be deleted.** ``prune_sessions``
  removes revoked / expired / idle sessions, and deleting a lexicon with no
  learning records cascades to the caller's ``user_lexicon`` row. Those removals
  are reported under ``rows_pruned`` -- never swallowed -- and a *surviving* row
  must still hash identically, so a swapped ``token_hash`` or a row re-pointed at
  another owner is still a failure.

Neither allowance is a blanket exemption: a table is only row-tolerant when live
code genuinely deletes its rows, and only the listed columns leave the identity.

This module never writes to the databases it inspects: every connection is
opened read-only.
"""

from __future__ import annotations

import hashlib
import importlib.util
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

#: Tables whose rows may legitimately be DELETED while the application runs.
#:
#: ``prune_sessions`` deletes revoked, absolutely expired and idle session rows,
#: and ``DELETE /api/lexicons/{id}`` cascades to the caller's ``user_lexicon``
#: row when the lexicon holds no learning records. A baseline recorded earlier
#: therefore legitimately loses rows in exactly these two tables, so for them a
#: falling row count and missing baseline rows are reported as ``rows_pruned``
#: instead of failing the verdict.
#:
#: The allowance is deliberately narrow and does NOT relax anything else: a
#: surviving row must still hash identically, so a swapped ``token_hash``, a
#: session re-pointed at another user, or a membership re-pointed at another
#: lexicon all still fail. No other table is listed -- every other row set only
#: grows, so a lost row there remains evidence of loss.
ROW_TOLERANT_TABLES: frozenset[str] = frozenset({"user_session", "user_lexicon"})

#: Columns excluded from row identities.
#:
#: Two reasons for an entry here, both about *when* a column changes rather than
#: how much it matters:
#:
#: 1. **Bookkeeping columns added by a migration.** The V1.2 migration adds
#:    ``user_id`` and ``lexicon_entry_id`` bridges. Hashing ``select *`` made every
#:    existing row look "modified" for that reason alone.
#: 2. **Columns the running application rewrites.** A login touches
#:    ``user_session.last_seen_at``; a review rewrites ``user_word_state.status``,
#:    ``next_review_at``, ``last_review`` and the counters; a settings save
#:    rewrites ``user_settings``; every update moves an ``updated_at``. These are
#:    all defined as *derived cache* in ``docs/PROJECT_ARCHITECTURE.md`` 4.3 --
#:    the truth is the append-only ``review_event``, which stays under the full
#:    strict row fingerprint. Excluding them is not "switching verification off".
#:
#: Anything not listed is still protected, and that includes the columns a
#: rewrite would have to forge:
#:
#: * ``user_session.token_hash``, ``user_id`` -- a swapped token or a session
#:   re-pointed at another account is a backdoor and must be caught.
#: * ``user_word_state.user_id`` / ``lexicon_entry_id`` / ``legacy_word_id`` --
#:   the ownership and bridge columns; a re-pointed row is cross-user corruption.
#: * ``first_seen`` / ``created_at`` / ``started_at`` / ``expires_at`` /
#:   ``user_agent`` -- written once, never rewritten, so a change is evidence.
#: * `app_setting.key` / `user.username` -- identities of configuration and
#:   accounts; losing one is loss.
#:
#: RULE FOR FUTURE WORK: the moment code starts writing a column that is not
#: listed here, add it here and re-record the baseline (``identity_columns`` is
#: frozen into the baseline file, so a code-only change has no effect).
IGNORED_COLUMNS: dict[str, frozenset[str]] = {
    "word": frozenset({"user_id", "lexicon_entry_id"}),
    "article": frozenset({"user_id"}),
    "review_event": frozenset({"user_id"}),
    "import_batch": frozenset({"user_id"}),
    "import_candidate": frozenset({"lexicon_entry_id"}),
    "history_event": frozenset({"user_id"}),
    # V1.2 runtime tables. Instance and account rows survive; their mutable
    # columns do not take part in the identity.
    "user": frozenset(
        {"display_name", "role", "is_active", "password_hash", "updated_at"}
    ),
    "user_session": frozenset({"last_seen_at", "revoked_at"}),
    "user_settings": frozenset(
        {"daily_new_words", "article_length", "onboarding_seen", "theme", "updated_at"}
    ),
    "user_lexicon": frozenset({"enabled", "daily_new_words"}),
    "user_word_state": frozenset(
        {
            "status",
            "last_review",
            "next_review_at",
            "recall_success",
            "recall_fail",
            "consecutive_failures",
            "context_exposure",
            "updated_at",
            "anchor_override",
            "semantic_note",
            "notes",
            "possible_issue",
        }
    ),
    "app_setting": frozenset({"value", "updated_at"}),
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
    retention_dir: Path | None = None,
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
        #: Baseline rows that legitimately disappeared from a row-tolerant table.
        #: Reported so a pruned row is never silently accepted.
        "pruned": [],
    }
    failures: list[str] = report["failures"]  # type: ignore[assignment]
    growth: list[str] = report["growth"]  # type: ignore[assignment]
    pruned: list[str] = report["pruned"]  # type: ignore[assignment]

    archived_history: dict[str, str] = {}
    archived_full: dict[str, tuple[list[str], str]] = {}
    history_checkpoint: dict[str, str] = {}
    if retention_dir is not None:
        # Loading by file path also works when this module is imported directly
        # from backend tests rather than as a tools package.
        module_path = Path(__file__).with_name("history_archive.py")
        spec = importlib.util.spec_from_file_location("history_archive", module_path)
        assert spec and spec.loader
        archive_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(archive_module)
        try:
            archived_history, archived_full, history_checkpoint, runs = (
                archive_module.load_committed_archives(retention_dir, baseline)
            )
            if runs:
                report["history_event_archived"] = {
                    "ids": sorted(archived_history, key=int),
                    "runs": runs,
                }
        except archive_module.ArchiveError as error:
            failures.append(f"history_event archive evidence invalid: {error}")

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
            recorded_rows = int(recorded.get("rows", 0))
            current_rows = int(current["rows"])
            entry["rows_baseline"] = recorded.get("rows")
            entry["rows_current"] = current["rows"]
            entry["status"] = "ok"

            recorded_hashes: dict[str, str] = recorded.get("row_hashes", {})  # type: ignore[assignment]
            current_hashes: dict[str, str] = current.get("row_hashes", {})  # type: ignore[assignment]
            if table == "history_event" and archived_history:
                for key in set(current_hashes) & set(archived_history):
                    columns, archived_hash = archived_full[key]
                    present_columns = {
                        row[1] for row in connection.execute('pragma table_info("history_event")')
                    }
                    if not set(columns) <= present_columns:
                        failures.append(f"history_event: archived id {key} columns are missing")
                        continue
                    projected = ", ".join(
                        '"' + column.replace('"', '""') + '"' for column in columns
                    )
                    live_row = connection.execute(
                        f'select {projected} from "history_event" where rowid = ?', (key,)
                    ).fetchone()
                    if live_row is None or row_hash(tuple(live_row)) != archived_hash:
                        failures.append(f"history_event: archived id {key} conflicts with live row")
                for key, previous_hash in history_checkpoint.items():
                    if key not in current_hashes and key not in archived_history:
                        failures.append(f"history_event: checkpoint row {key} is gone")
                    elif key in current_hashes and current_hashes[key] != previous_hash:
                        failures.append(f"history_event: checkpoint row {key} changed")
                logical_hashes = {**current_hashes, **archived_history}
                logical_rows = len(logical_hashes)
            else:
                logical_hashes = current_hashes
                logical_rows = current_rows
            lost: list[str] = []
            changed: list[str] = []
            added = 0
            if recorded_hashes:
                lost = sorted(set(recorded_hashes) - set(logical_hashes))
                changed = sorted(
                    key
                    for key in set(recorded_hashes) & set(logical_hashes)
                    if recorded_hashes[key] != logical_hashes[key]
                )
                added = len(set(logical_hashes) - set(recorded_hashes))
            # The row count and the row map are two witnesses of the same loss. A
            # baseline recorded without row hashes only has the first one, so take
            # whichever reports more.
            missing = max(recorded_rows - logical_rows, len(lost))

            if missing:
                if table in ROW_TOLERANT_TABLES:
                    # Deleting these rows is ordinary operation (session pruning,
                    # the cascade from deleting an empty lexicon). Reported as
                    # pruning -- visible, but not called loss.
                    entry["rows_pruned"] = missing
                    entry["status"] = "rows_pruned"
                    pruned.append(
                        f"{table}: {missing} of {recorded_rows} baseline rows removed "
                        "(legitimate pruning)"
                        + (f"; ids {lost[:10]}" if lost else "")
                    )
                elif logical_rows < recorded_rows:
                    failures.append(
                        f"{table}: row count fell from {recorded.get('rows')} to {current['rows']}"
                    )
                    entry["status"] = "rows_lost"
                else:
                    failures.append(
                        f"{table}: {len(lost)} baseline rows are gone: {lost[:10]}"
                    )
                    entry["status"] = "rows_lost"
            if changed:
                failures.append(
                    f"{table}: {len(changed)} baseline rows changed: {changed[:10]}"
                )
                entry["status"] = "rows_changed"
            if added:
                growth.append(f"{table}: +{added} rows")
            per_table[table] = entry

        report["tables"] = per_table
    finally:
        connection.close()

    report["verified"] = not failures
    return not failures, report
