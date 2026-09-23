"""The G6 history retention preview and its reviewable plan.

Two artifacts come out of this module:

* :func:`preview_history_retention` -- the read-only summary that answers "how many
  events would this policy archive, and why is each other event kept". It has no
  archive writer, deletion path or receipt API;
* :func:`build_plan` -- the **locked plan**: the candidate IDs, the UTC cutoff and a
  hash for every row in the table. The apply flow refuses to run unless the database
  still matches it exactly, so what an operator reviews is what gets deleted.

Neither function writes to the database: both read one SQLite snapshot with
``mode=ro``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

#: The confirmed retention window (G6 decision, 2026-09-23): events older than this
#: may be archived. The value lives here as the default; the operator-facing policy
#: object in ``app.history_retention`` is what actually decides.
RETENTION_DAYS = 365

#: The confirmed archive-eligible event types. Everything else is retained online,
#: including account, permission and data-disposal audits.
ARCHIVE_ELIGIBLE_EVENT_TYPES = (
    "login_failed",
    "reauth_failed",
    "user_login",
    "article_word_lookup",
)

PLAN_FORMAT_VERSION = 1

#: Fields covered by the plan digest. Everything that decides *what will be deleted*
#: has to be in here, or tampering with it would not invalidate the digest.
_DIGEST_FIELDS = (
    "format_version",
    "plan_type",
    "policy_status",
    "production_run_approved",
    "retention_days",
    "event_types",
    "cutoff_utc",
    "database",
    "candidate_ids",
    "candidates",
    "scan_hashes",
    "columns",
    "identity_columns",
    "max_history_event_id",
    "total_count",
)


class PlanError(ValueError):
    """The task cannot be planned from this database."""


@dataclass(frozen=True)
class HistoryScan:
    """One read snapshot of ``history_event``."""

    columns: list[str]
    #: ``id`` -> every column value, for every row in the table.
    rows: dict[str, list[object]]
    max_id: int | None
    #: Candidate IDs, sorted; the highest ID is never a candidate.
    candidate_ids: list[int]
    event_counts: dict[str, dict[str, int]]
    skipped: dict[str, int]
    cutoff: datetime
    now: datetime

    @property
    def total(self) -> int:
        return len(self.rows)


def _utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    try:
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def _require_aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return moment.astimezone(UTC)


#: Why a row is kept. ``None`` means it is a candidate for archiving.
SKIP_REASONS = (
    "highest_id",
    "not_allowlisted",
    "invalid_timestamp",
    "future_timestamp",
    "not_before_cutoff",
)


def classify_row(
    event_id: int,
    event_type: object,
    timestamp: object,
    *,
    cutoff: datetime,
    event_types: tuple[str, ...],
    now: datetime,
    max_id: int | None,
) -> str | None:
    """The one rule that decides whether a row may be archived.

    It lives in a single function because three callers have to agree exactly: the
    preview, the plan, and the re-check inside the deletion transaction. ``max_id`` is
    the protected highest ID -- frozen in the plan, so appending a new event does not
    silently change which old events the reviewed plan covers.
    """
    if max_id is not None and event_id == max_id:
        return "highest_id"
    if event_type not in event_types:
        return "not_allowlisted"
    parsed = _utc_timestamp(timestamp)
    if parsed is None:
        return "invalid_timestamp"
    if parsed > now:
        return "future_timestamp"
    if parsed >= cutoff:
        return "not_before_cutoff"
    return None


def candidate_ids_from(
    columns: list[str],
    rows: dict[str, list[object]],
    *,
    cutoff: datetime,
    event_types: tuple[str, ...],
    now: datetime,
    max_id: int | None,
) -> list[int]:
    """Re-derive the candidate set from rows already in memory."""
    if "timestamp" not in columns or "event_type" not in columns:
        raise PlanError("history_event is missing timestamp or event_type")
    timestamp_index = columns.index("timestamp")
    type_index = columns.index("event_type")
    ids: list[int] = []
    for key, values in rows.items():
        event_id = int(key)
        reason = classify_row(
            event_id,
            values[type_index],
            values[timestamp_index],
            cutoff=cutoff,
            event_types=event_types,
            now=now,
            max_id=max_id,
        )
        if reason is None:
            ids.append(event_id)
    return sorted(ids)


def _read_rows(
    database_path: Path,
) -> tuple[list[str], dict[str, list[object]]]:
    """Every column of every ``history_event`` row, keyed by ``id``.

    Reading the whole table keeps the candidate decision and the row hashes in the
    same snapshot: a drifted database cannot produce a plan that mixes two moments.
    """
    uri = Path(database_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        columns = [row[1] for row in connection.execute('pragma table_info("history_event")')]
        if not columns or "id" not in columns:
            raise PlanError("history_event has no id column")
        projected = ", ".join(f'"{column}"' for column in columns)
        rows = {
            str(row[0]): list(row[1:])
            for row in connection.execute(f'select rowid, {projected} from "history_event"')
        }
    return columns, rows


def scan_history_events(
    database_path: Path,
    *,
    cutoff: datetime,
    event_types: tuple[str, ...] = ARCHIVE_ELIGIBLE_EVENT_TYPES,
    now: datetime | None = None,
) -> HistoryScan:
    """Decide the candidate set from one read-only snapshot."""
    moment = _require_aware(now or datetime.now(UTC))
    cutoff = _require_aware(cutoff)
    columns, raw_rows = _read_rows(database_path)
    timestamp_index = columns.index("timestamp")
    type_index = columns.index("event_type")

    counts = {name: {"total": 0, "candidate": 0} for name in event_types}
    skipped = {reason: 0 for reason in SKIP_REASONS}
    candidate_ids: list[int] = []
    max_id = max((int(key) for key in raw_rows), default=None)
    for key, values in raw_rows.items():
        event_id = int(key)
        event_type = values[type_index]
        if event_type in counts:
            counts[event_type]["total"] += 1
        reason = classify_row(
            event_id,
            event_type,
            values[timestamp_index],
            cutoff=cutoff,
            event_types=event_types,
            now=moment,
            max_id=max_id,
        )
        if reason is None:
            candidate_ids.append(event_id)
            counts[event_type]["candidate"] += 1
        else:
            skipped[reason] += 1
    candidate_ids.sort()
    return HistoryScan(
        columns=columns,
        rows=raw_rows,
        max_id=max_id,
        candidate_ids=candidate_ids,
        event_counts=counts,
        skipped=skipped,
        cutoff=cutoff,
        now=moment,
    )


def _candidate_ids_digest(candidate_ids: list[int]) -> str:
    canonical = json.dumps(candidate_ids, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def preview_history_retention(
    database_path: Path,
    *,
    now: datetime | None = None,
    retention_days: int = RETENTION_DAYS,
) -> dict:
    """Count the candidates from one read snapshot, without writing anything."""
    moment = _require_aware(now or datetime.now(UTC))
    scan = scan_history_events(
        database_path, cutoff=moment - timedelta(days=retention_days), now=moment
    )
    return {
        "policy_status": "confirmed",
        "production_run_approved": False,
        "retention_days": retention_days,
        "event_types": list(ARCHIVE_ELIGIBLE_EVENT_TYPES),
        "cutoff_utc": _iso_z(scan.cutoff),
        "max_history_event_id": scan.max_id,
        "total_count": scan.total,
        "candidate_count": len(scan.candidate_ids),
        "candidate_ids_sha256": _candidate_ids_digest(scan.candidate_ids),
        "candidate_id_range": _id_range(scan.candidate_ids),
        "event_counts": scan.event_counts,
        "skipped": scan.skipped,
    }


def _iso_z(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _id_range(ids: list[int]) -> dict[str, int | None]:
    if not ids:
        return {"first": None, "last": None}
    return {"first": ids[0], "last": ids[-1]}


def plan_digest(plan: dict) -> str:
    """SHA-256 over the fields that decide which rows this plan would delete."""
    body = {field: plan.get(field) for field in _DIGEST_FIELDS}
    payload = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_plan(
    database_path: Path,
    *,
    baseline_path: Path,
    now: datetime | None = None,
    run_id: str | None = None,
    retention_days: int = RETENTION_DAYS,
    event_types: tuple[str, ...] = ARCHIVE_ELIGIBLE_EVENT_TYPES,
) -> dict:
    """Build the locked plan an operator reviews and the apply flow must match.

    Per-row hashes are recorded twice: the *full* row (every column, which is what the
    archive stores and the deletion must reproduce) and the *identity* columns frozen
    by the baseline (which is what the existing verifier compares history rows by).
    Every row that will be kept is hashed as well, so a run cannot proceed after
    something it is not allowed to remove has disappeared or changed.
    """
    from app.history_retention import RetentionError, load_baseline, retention_policy
    from app.verification import load_tool

    moment = _require_aware(now or datetime.now(UTC))
    policy = retention_policy()
    if retention_days != policy.retention_days:
        raise RetentionError(
            f"plan retention window {retention_days} differs from the configured "
            f"policy {policy.retention_days}"
        )
    verified_db = load_tool("verified_db")
    baseline = load_baseline(baseline_path)
    scan = scan_history_events(        database_path,
        cutoff=moment - timedelta(days=retention_days),
        event_types=event_types,
        now=moment,
    )
    if not scan.candidate_ids:
        raise RetentionError("no candidate events: nothing to archive, so no plan is made")

    identity_columns = (
        baseline.get("tables", {}).get("history_event", {}).get("identity_columns")
        or [column for column in scan.columns if column != "user_id"]
    )
    missing = set(identity_columns) - set(scan.columns)
    if missing:
        raise RetentionError(f"baseline history columns missing from the database: {sorted(missing)}")
    identity_indexes = [scan.columns.index(column) for column in identity_columns]

    candidates = {}
    for event_id in scan.candidate_ids:
        values = scan.rows[str(event_id)]
        candidates[str(event_id)] = {
            "full": verified_db.row_hash(tuple(values)),
            "identity": verified_db.row_hash(
                tuple(values[index] for index in identity_indexes)
            ),
        }
    plan: dict[str, Any] = {
        "format_version": PLAN_FORMAT_VERSION,
        "plan_type": "history_retention",
        "policy_status": "confirmed",
        # The retention *policy* is confirmed and implemented; a production *run* is
        # a separate approval. This flag is never set by planning.
        "production_run_approved": False,
        "created_utc": _iso_z(moment),
        "run_id": run_id or _new_run_id(moment),
        "retention_days": retention_days,
        "event_types": list(event_types),
        "cutoff_utc": _iso_z(scan.cutoff),
        "database": {
            "path": str(Path(database_path).resolve()),
            "revision": _revision(database_path),
            "max_history_event_id": scan.max_id,
            "total_count": scan.total,
        },
        "columns": scan.columns,
        "identity_columns": identity_columns,
        "max_history_event_id": scan.max_id,
        "total_count": scan.total,
        "candidate_count": len(scan.candidate_ids),
        "candidate_ids": scan.candidate_ids,
        "candidate_ids_sha256": _candidate_ids_digest(scan.candidate_ids),
        "candidate_id_range": _id_range(scan.candidate_ids),
        "candidates": candidates,
        "scan_hashes": {
            key: verified_db.row_hash(tuple(values)) for key, values in scan.rows.items()
        },
        "event_counts": scan.event_counts,
        "skipped": scan.skipped,
    }
    plan["plan_sha256"] = plan_digest(plan)
    # The plan is evidence, not a second copy of the audit trail: only hashes of the
    # row contents are recorded, never the rows themselves.
    plan["notes"] = [
        "candidates 只包含比 cutoff 严格更早、且在白名单内的 history_event；最高 ID 永不入选",
        "scan_hashes 覆盖当时存在的每一行（含不删除的行）：任何一行丢失或被改写都会使 apply 拒绝",
        "apply 只能删除 candidate_ids 中的 ID，并在同一事务内重新核对每一行的 hash",
    ]
    return plan


def _revision(database_path: Path) -> str | None:
    uri = Path(database_path).resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            row = connection.execute("select version_num from alembic_version").fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _new_run_id(moment: datetime) -> str:
    import secrets

    stamp = moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"hist-{stamp}-{secrets.token_hex(4)}"


def write_plan(plan: dict, path: Path) -> Path:
    """Write the plan once, refusing to overwrite previous evidence."""
    if plan_digest(plan) != plan.get("plan_sha256"):
        raise PlanError("plan digest does not match its contents")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as target:
        json.dump(plan, target, ensure_ascii=False, indent=2, sort_keys=True)
        target.write("\n")
    return path


def load_plan(path: Path) -> dict:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if plan.get("plan_type") != "history_retention":
        raise PlanError("not a history retention plan")
    if plan.get("format_version") != PLAN_FORMAT_VERSION:
        raise PlanError(f"unsupported plan format {plan.get('format_version')!r}")
    if plan_digest(plan) != plan.get("plan_sha256"):
        raise PlanError("plan digest does not match its contents")
    return plan
