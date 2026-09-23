"""G6 retention apply: the destructive half, behind a locked plan.

The read-only preview answers "what would be archived". This module is the only code
in the project that may actually remove `history_event` rows, and it does so under
rules that are meant to make an unreviewed deletion impossible:

* the plan (``app.history_retention_preview.build_plan``) fixes the candidate IDs, the
  UTC cutoff and a hash per row. Any drift -- a candidate changed, a candidate gone, a
  row the plan did not intend to touch gone or changed, a new candidate appearing --
  refuses the run;
* the pre-cleanup backup is taken with the SQLite **online backup API** into a
  uniquely named path that refuses to overwrite, and it must itself verify against the
  baseline before anything is deleted;
* the archive and a **pending** credential are written before the deletion, so a run
  that dies mid-way still has exact evidence of what it was about to remove;
* the deletion happens inside one ``BEGIN IMMEDIATE`` transaction, by explicit ID
  only, with the deleted count checked against the plan, and the candidate rows are
  re-hashed inside that transaction;
* after the commit the run is verified again with its own pending credential, and the
  `committed` credential is published only then, by an atomic rename.

Every failure leaves the database as it was, or -- if the commit already happened --
leaves verification failing on purpose: a loss is never explained by evidence the
operator has not published. See
``docs/V1.2-PHASE2.8-D-HISTORY-RETENTION-DESIGN.md`` §4.

Nothing here decides the policy. The retention window and the eligible event types
come from :func:`retention_policy`, and the window can be switched off entirely.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.history_retention_preview import (
    ARCHIVE_ELIGIBLE_EVENT_TYPES,
    RETENTION_DAYS,
    build_plan,
    candidate_ids_from,
    load_plan,
    plan_digest,
    write_plan,
)
from app.testing_guards import is_protected_database
from app.verification import load_tool

__all__ = [
    "ARCHIVE_ELIGIBLE_EVENT_TYPES",
    "MIN_RETENTION_DAYS",
    "RETENTION_DAYS",
    "RETENTION_DAYS_ENV",
    "RetentionError",
    "RetentionPolicy",
    "apply_plan",
    "build_plan",
    "load_plan",
    "plan_digest",
    "retention_policy",
    "write_plan",
]

#: Operator-facing switch for the retention window. ``0`` (or an explicit false-ish
#: value) disables cleanup entirely; anything else must be at least
#: :data:`MIN_RETENTION_DAYS`, so a shortened window cannot be smuggled in through a
#: CLI flag. Changing the window is a decision, not an argument.
RETENTION_DAYS_ENV = "VOCAB_HISTORY_EVENT_RETENTION_DAYS"
MIN_RETENTION_DAYS = 365
_DISABLED_VALUES = {"0", "false", "no", "off", ""}

#: How many IDs go into one DELETE statement. SQLite has a limit on bound parameters
#: (999 on older builds), and the count is checked across the batches.
_DELETE_BATCH_SIZE = 500


class RetentionError(RuntimeError):
    """The run was refused, or failed closed. Nothing may be assumed about the data."""


@dataclass(frozen=True)
class RetentionPolicy:
    """The confirmed online retention window and the event types eligible for it."""

    retention_days: int
    event_types: tuple[str, ...]
    enabled: bool
    source: str


def retention_policy(environ: Mapping[str, str] | None = None) -> RetentionPolicy:
    """Read the retention window from the environment.

    ``0``/false disables cleanup. A value below :data:`MIN_RETENTION_DAYS` (other than
    a disabled one) is refused rather than honoured: the 365-day window is the
    confirmed policy, and shortening it is a separate decision that must not be
    reachable from a single environment variable an operator can set by accident.
    """
    source = os.environ if environ is None else environ
    raw = str(source.get(RETENTION_DAYS_ENV, "")).strip().lower()
    if not raw:
        return RetentionPolicy(RETENTION_DAYS, ARCHIVE_ELIGIBLE_EVENT_TYPES, True, "default")
    if raw in _DISABLED_VALUES:
        return RetentionPolicy(RETENTION_DAYS, ARCHIVE_ELIGIBLE_EVENT_TYPES, False, "disabled")
    try:
        days = int(raw)
    except ValueError as error:
        raise RetentionError(
            f"{RETENTION_DAYS_ENV}={raw!r} is not a number of days"
        ) from error
    if days < MIN_RETENTION_DAYS:
        raise RetentionError(
            f"{RETENTION_DAYS_ENV}={days} is below the confirmed minimum "
            f"{MIN_RETENTION_DAYS}; shortening the window needs its own decision"
        )
    return RetentionPolicy(days, ARCHIVE_ELIGIBLE_EVENT_TYPES, True, "configured")


def load_baseline(baseline_path: Path) -> dict[str, object]:
    verified_db = load_tool("verified_db")
    path = Path(baseline_path)
    if not path.is_file():
        raise RetentionError(f"baseline not found: {path}")
    try:
        return verified_db.load_baseline(path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RetentionError(f"baseline cannot be read: {error}") from error


def _normalized(path: Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RetentionError(message)


def apply_plan(
    database: Path,
    plan: dict,
    *,
    baseline_path: Path,
    evidence_dir: Path,
    drafts_dir: Path | None = None,
    allow_production: bool = False,
    now: datetime | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Archive and then remove exactly the planned rows, or change nothing.

    ``allow_production`` has to be passed explicitly for a protected database (the
    live one, a backup, a recovery copy): this batch never used it on real data, and
    a production run additionally needs the project owner's approval of one specific
    plan and run ID plus a maintenance window.
    """
    say = log or (lambda _message: None)
    database = Path(database)
    evidence_dir = Path(evidence_dir)
    drafts_dir = (
        Path(drafts_dir) if drafts_dir is not None else evidence_dir.parent / "history-retention-drafts"
    )
    moment = now or datetime.now(UTC)

    policy = retention_policy()
    if not policy.enabled:
        raise RetentionError(
            f"retention is switched off ({RETENTION_DAYS_ENV}=0); nothing was changed"
        )
    if is_protected_database(database) and not allow_production:
        raise RetentionError(
            f"{_normalized(database)} is a protected database (live data, a backup or a "
            "recovery copy). Pass --allow-production only when a production run has been "
            "explicitly approved for this plan and run ID."
        )
    if not database.is_file():
        raise RetentionError(f"database not found: {database}")

    if plan_digest(plan) != plan.get("plan_sha256"):
        raise RetentionError("the plan was modified after it was written (digest mismatch)")
    _require(
        plan.get("retention_days") == policy.retention_days,
        f"the plan uses a {plan.get('retention_days')}-day window but the policy says "
        f"{policy.retention_days}",
    )
    _require(
        list(plan.get("event_types") or []) == list(policy.event_types),
        "the plan's eligible event types differ from the confirmed policy",
    )
    plan_path = (plan.get("database") or {}).get("path")
    _require(
        plan_path is not None and _normalized(Path(plan_path)) == _normalized(database),
        f"the plan was made for {plan_path!r}, not for {_normalized(database)}",
    )
    run_id = plan.get("run_id")
    history_archive = load_tool("history_archive")
    _require(
        isinstance(run_id, str) and bool(history_archive.RUN_ID.fullmatch(run_id)),
        "the plan has no usable run ID",
    )
    ids = [int(value) for value in plan.get("candidate_ids") or []]
    _require(bool(ids), "the plan has no candidates; refusing an empty deletion")
    baseline = load_baseline(baseline_path)
    say(f"计划 {run_id}: {len(ids)} 个候选，cutoff {plan.get('cutoff_utc')}")

    # 1. Drift first: the reviewed plan must still describe this database.
    problems = _drift_problems(_read_rows(database, plan), plan, moment)
    if problems:
        raise RetentionError("database drift since the plan: " + "; ".join(problems))
    say("漂移检查通过：候选与不删除的行都与计划一致")

    # 2. A consistent, uniquely named pre-cleanup backup that refuses to overwrite.
    drafts_dir.mkdir(parents=True, exist_ok=True)
    backup = drafts_dir / f"{run_id}.before.db"
    _create_pre_backup(database, backup)
    say(f"清理前备份：{backup}")

    # 3. The backup must preserve the baseline and match the plan row for row.
    _verify_backup(backup, baseline, plan, evidence_dir)
    say("备份已核验：baseline 行、revision、integrity 与外键均通过")

    # 4. Archive the exact rows plus a pending credential, before any deletion.
    manifest = _write_archive(backup, drafts_dir, evidence_dir, baseline, plan)
    say(f"归档与待提交凭证：{manifest}")

    # 5. The pending evidence has to verify before the deletion is attempted.
    _verify_run(
        database, baseline, drafts_dir, run_id, stage="删除前", evidence_dir=evidence_dir
    )
    say("删除前核验通过（含待提交凭证）")

    # 6. One transaction: re-verify inside it, delete by explicit ID, check the count.
    deleted = _commit_deletion(database, plan, moment)
    say(f"事务已提交：按明确 ID 删除 {deleted} 行")

    # 7. Verify again with this run's own pending credential.
    _verify_run(
        database, baseline, drafts_dir, run_id, stage="提交后", evidence_dir=evidence_dir
    )
    say("提交后核验通过（待提交凭证）")

    # 8. Publish the committed credential -- the atomic commit point of the evidence.
    try:
        committed = _publish_credential(history_archive, drafts_dir, evidence_dir, run_id)
    except RetentionError as error:
        # The deletion is already committed, so this is the one failure that cannot be
        # undone by itself: say so plainly and name the file that holds every row.
        raise RetentionError(
            f"{error}。事务已经提交，但 committed 凭证未发布（故障关闭）："
            "此时任何核验都会失败，这是有意的。请先停止写入，再从 "
            f"{drafts_dir / f'{run_id}.before.db'}（若已移入凭证目录则为 "
            f"{evidence_dir / f'{run_id}.before.db'}）恢复到隔离副本并核验，"
            "不要通过补写凭证或重录 baseline 让它看起来通过。"
        ) from error
    say(f"已发布 committed 凭证：{committed}")

    # 9. The published state must verify through the ordinary, public reader.
    verified, report = _compare(database, baseline, evidence_dir)
    if not verified:
        raise RetentionError(
            "the published evidence does not verify: "
            + "; ".join(report["failures"])
            + f". Restore from {evidence_dir / f'{run_id}.before.db'} before continuing."
        )
    say("公开发布后核验通过：verify_backup 现在可以接受这次清理")

    return {
        "run_id": run_id,
        "candidate_ids": ids,
        "deleted": deleted,
        "cutoff_utc": plan.get("cutoff_utc"),
        "retention_days": plan.get("retention_days"),
        "drafts_dir": str(drafts_dir),
        "pre_backup": str(evidence_dir / f"{run_id}.before.db"),
        "archive": str(evidence_dir / f"{run_id}.archive.jsonl"),
        "committed_manifest": str(committed),
        "verification": report,
    }


# --- steps -------------------------------------------------------------------


def _read_rows(database: Path, plan: dict) -> dict[str, list[object]]:
    """Read the planned columns of every history row from a read-only connection."""
    columns = plan.get("columns")
    _require(isinstance(columns, list) and bool(columns), "the plan has no column list")
    projected = ", ".join(f'"{column}"' for column in columns)
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    try:
        with contextlib.closing(sqlite3.connect(uri, uri=True)) as connection:
            return {
                str(row[0]): list(row[1:])
                for row in connection.execute(f'select rowid, {projected} from "history_event"')
            }
    except sqlite3.Error as error:
        raise RetentionError(f"history_event cannot be read with the plan's columns: {error}") from error


def _drift_problems(rows: dict[str, list[object]], plan: dict, moment: datetime) -> list[str]:
    """Every way the current rows disagree with the reviewed plan."""
    verified_db = load_tool("verified_db")
    current = {key: verified_db.row_hash(tuple(values)) for key, values in rows.items()}
    planned_candidates = {str(key): value for key, value in (plan.get("candidates") or {}).items()}
    problems: list[str] = []
    for key, entry in planned_candidates.items():
        if key not in current:
            problems.append(f"候选行 {key} 已不存在")
        elif current[key] != entry.get("full"):
            problems.append(f"候选行 {key} 的内容已改变")
    for key, digest in (plan.get("scan_hashes") or {}).items():
        if key in planned_candidates:
            continue
        if key not in current:
            problems.append(f"计划不删除的行 {key} 已丢失")
        elif current[key] != digest:
            problems.append(f"计划不删除的行 {key} 已被改写")
    recomputed = candidate_ids_from(
        plan.get("columns") or [],
        rows,
        cutoff=_parse_cutoff(plan),
        event_types=tuple(plan.get("event_types") or ()),
        now=moment,
        max_id=plan.get("max_history_event_id"),
    )
    if recomputed != [int(value) for value in plan.get("candidate_ids") or []]:
        problems.append(
            f"候选集合已改变：现在为 {recomputed}，计划为 {plan.get('candidate_ids')}"
        )
    return problems


def _parse_cutoff(plan: dict) -> datetime:
    raw = plan.get("cutoff_utc")
    _require(isinstance(raw, str) and bool(raw), "the plan has no cutoff")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise RetentionError(f"the plan cutoff {raw!r} cannot be parsed") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _create_pre_backup(database: Path, target: Path) -> Path:
    """Snapshot the live database with the SQLite online backup API.

    A file copy is not enough: the database runs in WAL mode, and pages committed to
    the ``-wal`` file but not yet checkpointed would be missing from a plain copy. The
    target is created only if it does not exist, so an earlier run's evidence can
    never be overwritten.

    The copy is then taken out of WAL mode, so the evidence is one self-contained file:
    the live database is WAL, and a backup handed over without its ``-wal`` would be
    exactly the incomplete copy the D-3 rule warns about. Checkpointing and switching
    to the rollback journal leaves nothing behind for a restore to get wrong.
    """
    if target.exists():
        raise RetentionError(f"refusing to overwrite existing evidence: {target}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(f"file:{Path(database).as_posix()}?mode=ro", uri=True)
        try:
            destination = sqlite3.connect(target)
            try:
                source.backup(destination)
                destination.execute("pragma journal_mode=delete")
            finally:
                destination.close()
        finally:
            source.close()
    except sqlite3.Error as error:
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)
        raise RetentionError(f"could not create the pre-cleanup backup: {error}") from error
    for suffix in ("-wal", "-shm"):
        with contextlib.suppress(OSError):
            target.with_name(target.name + suffix).unlink(missing_ok=True)
    return target


def _verify_backup(backup: Path, baseline: dict, plan: dict, evidence_dir: Path) -> None:
    """The backup must preserve the baseline *and* reproduce the plan's rows.

    The comparison includes the runs already published: their archived rows are
    legitimately absent from this snapshot, and nothing else may be.
    """
    verified, report = _compare(backup, baseline, evidence_dir)
    if not verified:
        raise RetentionError(
            "the pre-cleanup backup does not preserve the baseline: "
            + "; ".join(report["failures"])
        )
    history_archive = load_tool("history_archive")
    columns, rows = history_archive.read_history_rows(backup)
    if columns != plan.get("columns"):
        raise RetentionError(
            f"history_event columns changed since the plan: {columns} != {plan.get('columns')}"
        )
    problems = _drift_problems(rows, plan, datetime.now(UTC))
    if problems:
        raise RetentionError("the pre-cleanup backup does not match the plan: " + "; ".join(problems))


def _write_archive(
    backup: Path, drafts_dir: Path, evidence_dir: Path, baseline: dict, plan: dict
) -> Path:
    history_archive = load_tool("history_archive")
    try:
        return history_archive.create_archive(
            backup,
            drafts_dir,
            baseline,
            [int(value) for value in plan["candidate_ids"]],
            run_id=str(plan["run_id"]),
            state=history_archive.PENDING_STATE,
            pre_backup_in_place=True,
            # The chain continues from the published runs, not from the draft.
            chain_dir=evidence_dir,
            extra={
                "plan_sha256": plan.get("plan_sha256"),
                "cutoff_utc": plan.get("cutoff_utc"),
                "retention_days": plan.get("retention_days"),
                "event_types": plan.get("event_types"),
            },
        )
    except history_archive.ArchiveError as error:
        raise RetentionError(f"could not write the archive: {error}") from error


def _compare(
    database: Path,
    baseline: dict,
    retention_dir: Path | None,
    run_id: str | None = None,
    chain_dir: Path | None = None,
):
    verified_db = load_tool("verified_db")
    return verified_db.compare_against_baseline(
        database,
        baseline,
        retention_dir=retention_dir,
        allow_pending_run=run_id,
        chain_dir=chain_dir,
    )


def _verify_run(
    database: Path,
    baseline: dict,
    drafts_dir: Path,
    run_id: str,
    *,
    stage: str,
    evidence_dir: Path | None = None,
) -> dict:
    """Verify the database as if this unpublished run were the latest evidence.

    The chain is read from the published directory: this run's own draft continues it,
    so the check has to look in both places.
    """
    verified, report = _compare(database, baseline, drafts_dir, run_id, evidence_dir)
    if not verified:
        raise RetentionError(f"{stage}核验未通过：" + "; ".join(report["failures"]))
    return report


def _delete_rows(connection: sqlite3.Connection, ids: list[int]) -> int:
    """Delete exactly these IDs in bounded batches; return how many rows went."""
    if not ids:
        raise RetentionError("refusing to delete an empty candidate set")
    deleted = 0
    for start in range(0, len(ids), _DELETE_BATCH_SIZE):
        batch = ids[start : start + _DELETE_BATCH_SIZE]
        placeholders = ",".join("?" for _ in batch)
        cursor = connection.execute(
            f"delete from history_event where id in ({placeholders})", batch
        )
        deleted += cursor.rowcount
    return deleted


def _commit_deletion(database: Path, plan: dict, moment: datetime) -> int:
    """Re-verify, delete by explicit ID, check the count, all in one transaction.

    ``BEGIN IMMEDIATE`` takes the write lock up front, so the rows cannot change
    between the re-verification and the delete. Any failure rolls the whole
    transaction back: the database is left exactly as it was.
    """
    ids = [int(value) for value in plan["candidate_ids"]]
    committed = False
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        problems = _drift_problems(_read_rows_locked(connection, plan), plan, moment)
        if problems:
            raise RetentionError("database drift inside the transaction: " + "; ".join(problems))
        deleted = _delete_rows(connection, ids)
        if deleted != len(ids):
            raise RetentionError(
                f"deleted {deleted} rows but the plan lists {len(ids)}; rolling back"
            )
        connection.execute("COMMIT")
        committed = True
        return deleted
    except sqlite3.Error as error:
        raise RetentionError(f"the deletion transaction failed: {error}") from error
    finally:
        if not committed:
            with contextlib.suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
        connection.close()


def _read_rows_locked(connection: sqlite3.Connection, plan: dict) -> dict[str, list[object]]:
    columns = plan.get("columns") or []
    projected = ", ".join(f'"{column}"' for column in columns)
    try:
        return {
            str(row[0]): list(row[1:])
            for row in connection.execute(f'select rowid, {projected} from "history_event"')
        }
    except sqlite3.Error as error:
        raise RetentionError(f"history_event cannot be read inside the transaction: {error}") from error


def _publish_credential(
    history_archive, drafts_dir: Path, evidence_dir: Path, run_id: str
) -> Path:
    """Move this run's evidence into place, publishing the manifest last.

    The manifest rename is what makes the run readable as evidence. Moving the archive
    and the backup first means a crash in between leaves the evidence directory without
    a committed manifest -- verification keeps failing, which is the point.
    """
    evidence_dir.mkdir(parents=True, exist_ok=True)
    for name in (f"{run_id}.archive.jsonl", f"{run_id}.before.db"):
        source = drafts_dir / name
        target = evidence_dir / name
        if not source.is_file():
            raise RetentionError(f"evidence is missing from the draft: {source}")
        if target.exists():
            raise RetentionError(f"refusing to overwrite existing evidence: {target}")
        os.replace(source, target)
    pending_name = f"{run_id}.manifest.pending.json"
    pending_target = evidence_dir / pending_name
    if pending_target.exists():
        raise RetentionError(f"refusing to overwrite existing evidence: {pending_target}")
    os.replace(drafts_dir / pending_name, pending_target)
    try:
        return history_archive.publish_archive(evidence_dir, run_id)
    except history_archive.ArchiveError as error:
        raise RetentionError(f"could not publish the committed credential: {error}") from error
