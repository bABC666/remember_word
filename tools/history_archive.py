"""Versioned, read-verifiable evidence for archived history_event rows.

The writer builds an archive from an existing consistent SQLite snapshot and records
exactly which rows it covers; the reader re-derives every hash from that snapshot, so
an archive can only ever explain the rows it actually contains. Neither function
selects candidates, approves a retention policy, or deletes anything -- that decision
belongs to the operator flow in ``app.history_retention``.

A run is written in two phases. The manifest is created **pending**
(``<run_id>.manifest.pending.json``) and is renamed to ``<run_id>.manifest.json`` --
the atomic publication step -- only after the deletion has been committed and
verified. The public reader accepts committed manifests only, so a run that crashed
between the commit and the publication explains nothing and verification fails; see
``docs/V1.2-PHASE2.8-D-HISTORY-RETENTION-DESIGN.md`` §4.2.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

FORMAT_VERSION = 1
RUN_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")

#: The published state of a manifest. ``pending`` is accepted only by a reader that
#: was explicitly pointed at that one run, which is how the apply flow checks its own
#: work before publishing.
COMMITTED_STATE = "committed"
PENDING_STATE = "pending"


class ArchiveError(ValueError):
    """An archive cannot justify any missing database row."""


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _baseline_digest(baseline: dict[str, object]) -> str:
    return hashlib.sha256(_json_bytes(baseline)).hexdigest()


def _row_hash(values: list[object]) -> str:
    # Match verified_db.row_hash exactly, including its byte normalization.
    normalized = [("b", value.hex()) if isinstance(value, bytes) else ("v", value)
                  for value in values]
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _encode(value: object) -> object:
    return {"blob_hex": value.hex()} if isinstance(value, bytes) else value


def _decode(value: object) -> object:
    if isinstance(value, dict):
        if set(value) != {"blob_hex"} or not isinstance(value["blob_hex"], str):
            raise ArchiveError("invalid encoded SQLite value")
        try:
            return bytes.fromhex(value["blob_hex"])
        except ValueError as error:
            raise ArchiveError("invalid BLOB hex") from error
    if value is None or isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return value
    raise ArchiveError("unsupported encoded SQLite value")


def read_history_rows(database: Path) -> tuple[list[str], dict[str, list[object]]]:
    """All ``history_event`` columns plus every row, keyed by its ID.

    Public because the retention planner needs the same view of the table that the
    archive writer uses: a plan and an archive must never disagree about which
    columns exist or what a row contains.
    """
    return _history_rows(database)


def _history_rows(database: Path) -> tuple[list[str], dict[str, list[object]]]:
    if not database.is_file():
        raise ArchiveError(f"pre-backup missing: {database}")
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as connection:
        columns = [row[1] for row in connection.execute('pragma table_info("history_event")')]
        if not columns or "id" not in columns:
            raise ArchiveError("history_event has no id column")
        projected = ", ".join(f'"{column}"' for column in columns)
        rows = {
            str(row[0]): list(row[1:])
            for row in connection.execute(f'select rowid, {projected} from "history_event"')
        }
    for key, values in rows.items():
        if str(values[columns.index("id")]) != key:
            raise ArchiveError("history_event id differs from rowid")
    return columns, rows


def _safe_file(directory: Path, name: object) -> Path:
    if not isinstance(name, str) or not name or Path(name).name != name or "\\" in name:
        raise ArchiveError("archive path must be a plain filename")
    return directory / name


def _manifest_names(run_id: str) -> tuple[str, str]:
    return f"{run_id}.manifest.json", f"{run_id}.manifest.pending.json"


def _verify_pre_backup(
    database: Path, baseline: dict[str, object], prior_archived: dict[str, str]
) -> None:
    """Require the source snapshot to preserve the original baseline too."""
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as connection:
        if connection.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise ArchiveError("pre-backup integrity_check failed")
        if connection.execute("pragma foreign_key_check").fetchone() is not None:
            raise ArchiveError("pre-backup foreign_key_check failed")
        revision = connection.execute("select version_num from alembic_version").fetchone()
        if revision is None or revision[0] != baseline.get("alembic_revision"):
            raise ArchiveError("pre-backup revision differs from baseline")
        present = {
            row[0] for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        }
        for table, recorded in baseline.get("tables", {}).items():
            if table not in present:
                raise ArchiveError(f"pre-backup missing table {table}")
            columns = recorded.get("identity_columns")
            if not columns or "row_hashes" not in recorded:
                raise ArchiveError(f"baseline lacks row hashes for {table}")
            table_sql = '"' + table.replace('"', '""') + '"'
            projected = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
            try:
                current = {
                    str(row[0]): _row_hash(list(row[1:]))
                    for row in connection.execute(f"select rowid, {projected} from {table_sql}")
                }
            except sqlite3.Error:
                current = {
                    str(index): _row_hash(list(row))
                    for index, row in enumerate(connection.execute(f"select {projected} from {table_sql}"))
                }
            if table == "history_event":
                for key in set(current) & set(prior_archived):
                    if current[key] != prior_archived[key]:
                        raise ArchiveError(f"pre-backup conflicts with archived history ID {key}")
                current = {**current, **prior_archived}
            recorded_hashes = recorded["row_hashes"]
            lost = set(recorded_hashes) - set(current)
            changed = {key for key in set(recorded_hashes) & set(current)
                       if recorded_hashes[key] != current[key]}
            row_loss = len(current) < int(recorded["rows"])
            if (lost or row_loss) and table not in {"user_session", "user_lexicon"}:
                raise ArchiveError(f"pre-backup lost baseline rows in {table}")
            if changed:
                raise ArchiveError(f"pre-backup changed baseline rows in {table}")


def create_archive(
    pre_backup: Path,
    evidence_dir: Path,
    baseline: dict[str, object],
    row_ids: list[int],
    *,
    run_id: str,
    state: str = PENDING_STATE,
    extra: dict[str, Any] | None = None,
    pre_backup_in_place: bool = False,
    chain_dir: Path | None = None,
) -> Path:
    """Serialize exact rows from a snapshot; never modify the source database.

    ``state`` decides the manifest name: a run is written *pending* and only becomes
    readable evidence once :func:`publish_archive` renames it, so a crash between the
    commit and the publication cannot be mistaken for a completed cleanup. The writer
    itself never decides which rows may go.

    ``pre_backup_in_place`` says the snapshot is already at
    ``<run_id>.before.db``: the operator flow creates that file itself with the SQLite
    online backup API and verifies *that* file, so it must be the very file the
    manifest points at rather than a second copy.

    ``chain_dir`` is where the previously *published* runs live. The operator flow
    writes this run's files into a draft directory and publishes them into the
    evidence directory afterwards, but the manifest chain has to continue from the
    evidence directory -- otherwise the published run would claim to be the first one.
    """
    if not RUN_ID.fullmatch(run_id):
        raise ArchiveError("invalid run id")
    if state not in {PENDING_STATE, COMMITTED_STATE}:
        raise ArchiveError("invalid manifest state")
    if not row_ids or row_ids != sorted(set(row_ids)) or any(row_id <= 0 for row_id in row_ids):
        raise ArchiveError("row IDs must be nonempty, positive, sorted and unique")
    columns, rows = _history_rows(pre_backup)
    keys = [str(row_id) for row_id in row_ids]
    if any(key not in rows for key in keys):
        raise ArchiveError("archived row missing from pre-backup")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    chain = Path(chain_dir) if chain_dir is not None else evidence_dir
    committed_name, pending_name = _manifest_names(run_id)
    manifest_path = evidence_dir / (committed_name if state == COMMITTED_STATE else pending_name)
    archive_path = evidence_dir / f"{run_id}.archive.jsonl"
    backup_path = evidence_dir / f"{run_id}.before.db"
    if pre_backup_in_place:
        if Path(pre_backup).resolve() != backup_path.resolve():
            raise ArchiveError(
                "a pre-backup written in place must already be this run's before.db"
            )
    elif any(path.exists() for path in (manifest_path, archive_path, backup_path)):
        raise ArchiveError("run evidence already exists")
    if manifest_path.exists() or archive_path.exists():
        raise ArchiveError("run evidence already exists")
    # An unpublished run is an unfinished cleanup: refuse to stack another run on top
    # of it rather than silently reordering the manifest chain.
    for candidate in evidence_dir.glob("*.manifest.pending.json"):
        if candidate.name != pending_name:
            raise ArchiveError(f"unpublished run {candidate.name} must be resolved first")
    _previous_archives, _previous_full, _previous_checkpoint, previous_runs = load_archives(
        chain, baseline
    )
    _verify_pre_backup(pre_backup, baseline, _previous_archives)
    previous_manifest = (
        chain / f"{previous_runs[-1]['run_id']}.manifest.json"
        if previous_runs else None
    )
    if not pre_backup_in_place:
        shutil.copyfile(pre_backup, backup_path)
        if _sha256_file(backup_path) != _sha256_file(pre_backup):
            raise ArchiveError("pre-backup copy differs from source")
    identity_columns = baseline.get("tables", {}).get("history_event", {}).get(
        "identity_columns"
    ) or [column for column in columns if column != "user_id"]
    if not set(identity_columns) <= set(columns):
        raise ArchiveError("baseline history columns are not in pre-backup")
    identity_indexes = [columns.index(column) for column in identity_columns]
    lines = [{"format_version": FORMAT_VERSION, "run_id": run_id, "columns": columns}]
    lines.extend({"id": int(key), "values": [_encode(value) for value in rows[key]]}
                 for key in keys)
    archive_path.write_bytes(b"".join(_json_bytes(line) + b"\n" for line in lines))
    manifest: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "state": state,
        "run_id": run_id,
        "sequence": len(previous_runs) + 1,
        "previous_manifest_sha256": (
            _sha256_file(previous_manifest) if previous_manifest else None
        ),
        "baseline_digest": _baseline_digest(baseline),
        "pre_backup_file": backup_path.name,
        "pre_backup_sha256": _sha256_file(backup_path),
        "archive_file": archive_path.name,
        "archive_sha256": _sha256_file(archive_path),
        "columns": columns,
        "identity_columns": identity_columns,
        "row_ids": row_ids,
        "full_hashes": {key: _row_hash(rows[key]) for key in keys},
        "identity_hashes": {
            key: _row_hash([rows[key][index] for index in identity_indexes]) for key in keys
        },
        "pre_history_identity_hashes": {
            key: _row_hash([values[index] for index in identity_indexes])
            for key, values in rows.items()
        },
        # Evidence about the reviewed plan this run implements. The reader ignores
        # unknown fields; keeping them here ties the two artifacts together.
        "plan_sha256": (extra or {}).get("plan_sha256"),
        "cutoff_utc": (extra or {}).get("cutoff_utc"),
        "retention_days": (extra or {}).get("retention_days"),
        "event_types": (extra or {}).get("event_types"),
    }
    manifest_path.write_bytes(_json_bytes(manifest) + b"\n")
    return manifest_path


def create_committed_archive(
    pre_backup: Path,
    evidence_dir: Path,
    baseline: dict[str, object],
    row_ids: list[int],
    *,
    run_id: str,
) -> Path:
    """Write a manifest that is committed from the start.

    This is how fixtures and tests build evidence; the operator flow writes a pending
    manifest and publishes it only after its deletion has been verified.
    """
    return create_archive(
        pre_backup, evidence_dir, baseline, row_ids, run_id=run_id, state=COMMITTED_STATE
    )


def publish_archive(evidence_dir: Path, run_id: str) -> Path:
    """Atomically turn one pending manifest into the committed credential.

    The rename is the commit point of the whole procedure, but the *content* has to
    change with it: a manifest says which state it is in, and the public reader accepts
    ``committed`` only. So the committed bytes are written to a temporary file first
    (never leaving a half-written manifest under the final name), the pending manifest
    is removed, and only then is the finished file renamed into place. A crash in
    between leaves no committed manifest at all, which is the safe direction.
    """
    if not RUN_ID.fullmatch(run_id):
        raise ArchiveError("invalid run id")
    committed_name, pending_name = _manifest_names(run_id)
    pending = evidence_dir / pending_name
    committed = evidence_dir / committed_name
    if not pending.is_file():
        raise ArchiveError("no pending manifest to publish")
    if committed.exists():
        raise ArchiveError("committed manifest already exists")
    try:
        manifest = json.loads(pending.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ArchiveError(f"pending manifest cannot be read: {error}") from error
    if manifest.get("state") != PENDING_STATE:
        raise ArchiveError("only a pending manifest can be published")
    manifest["state"] = COMMITTED_STATE
    temporary = evidence_dir / f"{run_id}.manifest.publishing"
    try:
        temporary.write_bytes(_json_bytes(manifest) + b"\n")
        pending.unlink()
        os.replace(temporary, committed)
    except OSError as error:
        raise ArchiveError(f"could not publish the manifest: {error}") from error
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
    return committed


def load_archives(
    evidence_dir: Path,
    baseline: dict[str, object],
    *,
    allow_pending_run: str | None = None,
    chain_dir: Path | None = None,
) -> tuple[
    dict[str, str],
    dict[str, tuple[list[str], str]],
    dict[str, str],
    list[dict[str, object]],
]:
    """Return exact archived identities, latest checkpoint and report entries.

    Any malformed or incomplete evidence raises ArchiveError. A missing directory
    means ordinary strict baseline verification, with no allowed history loss.

    ``allow_pending_run`` accepts exactly one unpublished run, and only for the flow
    that is publishing it right now: the ordinary verification path never sets it, so
    a pending manifest alone never explains a missing row.

    ``chain_dir`` is the directory the published runs are read from. It defaults to
    ``evidence_dir`` -- the only arrangement the public verification path uses. The
    operator flow keeps this run's unpublished files in its own directory, so it reads
    the chain from the published directory and the pending run from the draft; both
    directories are checked for stray files, and the pending run must still continue
    the chain.
    """
    published_dir = Path(chain_dir) if chain_dir is not None else Path(evidence_dir)
    pending_dir = Path(evidence_dir)
    for directory in {published_dir, pending_dir}:
        if directory.exists() and not directory.is_dir():
            raise ArchiveError("evidence path is not a directory")
    if not published_dir.exists() and not pending_dir.exists():
        return {}, {}, {}, []

    accepted_pending: Path | None = None
    if allow_pending_run is not None:
        if not RUN_ID.fullmatch(allow_pending_run):
            raise ArchiveError("invalid run id")
        candidate = pending_dir / _manifest_names(allow_pending_run)[1]
        accepted_pending = candidate if candidate.is_file() else None
    if accepted_pending is None and allow_pending_run is not None:
        try:
            has_files = pending_dir.exists() and any(pending_dir.iterdir())
        except OSError as error:
            raise ArchiveError(f"cannot read evidence directory: {error}") from error
        if has_files and published_dir != pending_dir:
            raise ArchiveError("the draft directory has no pending manifest for this run")
    try:
        committed = list(published_dir.glob("*.manifest.json")) if published_dir.exists() else []
    except OSError as error:
        raise ArchiveError(f"cannot read evidence directory: {error}") from error
    # A directory that holds an in-progress run is expected to have files but no
    # committed manifest yet; every other directory with files and no manifest is an
    # unresolved cleanup and refuses to be read as "nothing was ever archived".
    pending_in_place = accepted_pending is not None and published_dir == pending_dir
    if not committed and not pending_in_place:
        try:
            has_files = published_dir.exists() and any(published_dir.iterdir())
        except OSError as error:
            raise ArchiveError(f"cannot read evidence directory: {error}") from error
        if has_files:
            raise ArchiveError("evidence directory contains no committed manifest")
    # Each entry is (manifest path, the directory its files live in, expected state).
    entries: list[tuple[Path, Path, str]] = [
        (path, published_dir, COMMITTED_STATE) for path in committed
    ]
    if accepted_pending is not None:
        # Same directory or not, the pending run is the last link of the chain; the
        # per-directory file check below then expects exactly its three files.
        entries.append((accepted_pending, pending_dir, PENDING_STATE))
    try:
        entries.sort(key=lambda entry: json.loads(entry[0].read_text(encoding="utf-8"))["sequence"])
    except (OSError, KeyError, TypeError, UnicodeError, json.JSONDecodeError) as error:
        raise ArchiveError(f"invalid archive manifest sequence: {error}") from error
    archived: dict[str, str] = {}
    archived_full: dict[str, tuple[list[str], str]] = {}
    checkpoint: dict[str, str] = {}
    reports: list[dict[str, object]] = []
    previous_manifest: Path | None = None
    expected_by_dir: dict[Path, set[str]] = {}
    for position, (manifest_path, directory, expected_state) in enumerate(entries):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest["format_version"] != FORMAT_VERSION or manifest["state"] != expected_state:
                raise ArchiveError(f"archive manifest is not {expected_state} v1")
            if expected_state == PENDING_STATE and position != len(entries) - 1:
                raise ArchiveError("a pending run cannot precede a committed one")
            run_id = manifest["run_id"]
            if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
                raise ArchiveError("invalid run id")
            if manifest_path.name not in _manifest_names(run_id):
                raise ArchiveError("manifest filename and run ID differ")
            if manifest["sequence"] != len(reports) + 1 or manifest["previous_manifest_sha256"] != (
                _sha256_file(previous_manifest) if previous_manifest else None
            ):
                raise ArchiveError("archive manifest chain is incomplete or reordered")
            if manifest["baseline_digest"] != _baseline_digest(baseline):
                raise ArchiveError("manifest refers to another baseline")
            backup_path = _safe_file(directory, manifest["pre_backup_file"])
            archive_path = _safe_file(directory, manifest["archive_file"])
            if backup_path.name != f"{run_id}.before.db" or archive_path.name != f"{run_id}.archive.jsonl":
                raise ArchiveError("evidence filename and run ID differ")
            if _sha256_file(backup_path) != manifest["pre_backup_sha256"]:
                raise ArchiveError("pre-backup hash differs")
            _verify_pre_backup(backup_path, baseline, archived)
            if _sha256_file(archive_path) != manifest["archive_sha256"]:
                raise ArchiveError("archive hash differs")
            columns, pre_rows = _history_rows(backup_path)
            identity_columns = manifest["identity_columns"]
            baseline_columns = baseline.get("tables", {}).get("history_event", {}).get(
                "identity_columns"
            ) or [column for column in columns if column != "user_id"]
            if columns != manifest["columns"] or identity_columns != baseline_columns:
                raise ArchiveError("archive columns differ from baseline/pre-backup")
            indexes = [columns.index(column) for column in identity_columns]
            pre_hashes = {
                key: _row_hash([values[index] for index in indexes])
                for key, values in pre_rows.items()
            }
            if pre_hashes != manifest["pre_history_identity_hashes"]:
                raise ArchiveError("pre-backup history differs from manifest")
            if checkpoint:
                missing = set(checkpoint) - set(pre_hashes) - set(archived)
                changed = {key for key in set(checkpoint) & set(pre_hashes)
                           if checkpoint[key] != pre_hashes[key]}
                if missing or changed:
                    raise ArchiveError("history changed between archive runs")
            lines = archive_path.read_text(encoding="utf-8").splitlines()
            if not lines or json.loads(lines[0]) != {
                "format_version": FORMAT_VERSION, "run_id": run_id, "columns": columns
            }:
                raise ArchiveError("archive header differs")
            ids = manifest["row_ids"]
            if not ids or ids != sorted(set(ids)) or any(
                not isinstance(row_id, int) or row_id <= 0 for row_id in ids
            ):
                raise ArchiveError("invalid archived IDs")
            if len(lines) != len(ids) + 1:
                raise ArchiveError("archive row count differs")
            if set(manifest["full_hashes"]) != {str(i) for i in ids} or set(
                manifest["identity_hashes"]
            ) != {str(i) for i in ids}:
                raise ArchiveError("manifest row hashes differ from IDs")
            for row_id, line in zip(ids, lines[1:], strict=True):
                record = json.loads(line)
                key = str(row_id)
                if record["id"] != row_id or key not in pre_rows or key in archived:
                    raise ArchiveError("archive ID missing, duplicated or reordered")
                values = [_decode(value) for value in record["values"]]
                if len(values) != len(columns) or values != pre_rows[key]:
                    raise ArchiveError("archive row differs from pre-backup")
                if _row_hash(values) != manifest["full_hashes"][key]:
                    raise ArchiveError("archive full-row hash differs")
                identity_hash = _row_hash([values[index] for index in indexes])
                if identity_hash != manifest["identity_hashes"][key]:
                    raise ArchiveError("archive identity hash differs")
                archived[key] = identity_hash
                archived_full[key] = (columns, manifest["full_hashes"][key])
            checkpoint = pre_hashes
            reports.append({"run_id": run_id, "ids": [str(i) for i in ids],
                            "archive": str(archive_path),
                            "state": expected_state})
            previous_manifest = manifest_path
            names = expected_by_dir.setdefault(directory, set())
            names.add(manifest_path.name)
            names.add(f"{run_id}.archive.jsonl")
            names.add(f"{run_id}.before.db")
        except (OSError, KeyError, TypeError, UnicodeError, json.JSONDecodeError, sqlite3.Error) as error:
            raise ArchiveError(f"invalid archive evidence {manifest_path.name}: {error}") from error
    for directory, expected_files in expected_by_dir.items():
        try:
            actual_files = {path.name for path in directory.iterdir()}
        except OSError as error:
            raise ArchiveError(f"cannot read evidence directory: {error}") from error
        if actual_files != expected_files:
            raise ArchiveError("evidence directory contains missing or orphan files")
    return archived, archived_full, checkpoint, reports


def load_committed_archives(
    evidence_dir: Path, baseline: dict[str, object]
) -> tuple[dict[str, str], dict[str, tuple[list[str], str]], dict[str, str], list[dict[str, object]]]:
    """Strict, published-evidence-only reader used by every verification path."""
    return load_archives(evidence_dir, baseline)
