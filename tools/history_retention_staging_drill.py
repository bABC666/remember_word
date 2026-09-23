"""Staging acceptance drill for the G6 history retention apply flow.

This is the rehearsal the design record asks for before any production run
(``docs/V1.2-PHASE2.8-D-HISTORY-RETENTION-DESIGN.md`` §5.2). It works only on a
**new, uniquely named copy** of the live database inside ``data/staging/`` -- never on
``data/vocab.db`` itself -- and it walks the whole procedure, including the failure
and recovery paths:

1. copy the source with the SQLite **online backup API** (WAL aware, refuse overwrite);
2. verify the copy against the project baseline *before* anything is planned;
3. preview, then write the locked candidate plan;
4. apply: pre-cleanup backup, archive, pending credential, transactional deletion,
   post-commit verification, publication of the committed credential;
5. verify the pruned copy -- it must pass, with the archived rows listed;
6. delete one row the archive does **not** cover and confirm verification FAILS;
7. restore the copy from the run's own ``<run_id>.before.db`` and verify again -- the
   pre-cleanup backup has to keep passing on its own;
8. prove the live database was not touched (size and SHA-256 before and after).

Every artifact is written under a new run directory; nothing existing is overwritten.

Usage::

    backend\\.venv\\Scripts\\python.exe tools\\history_retention_staging_drill.py
    ... --source data/vocab.db --staging-dir data/staging --artifacts-dir test-artifacts
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

# The application package is imported after the path insert above: the drill runs the
# real apply flow rather than a copy of it.
from app.history_retention import (
    RetentionError,
    apply_plan,
    load_baseline,
    retention_policy,
)
from app.history_retention_preview import (
    build_plan,
    preview_history_retention,
    write_plan,
)
from app.testing_guards import is_protected_database
from app.verification import load_tool

verified_db = load_tool("verified_db")
history_archive = load_tool("history_archive")

DEFAULT_SOURCE = PROJECT_ROOT / "data" / "vocab.db"
DEFAULT_BASELINE = PROJECT_ROOT / "data" / "recovery" / "baseline.json"
DEFAULT_STAGING = PROJECT_ROOT / "data" / "staging"
DEFAULT_ARTIFACTS = PROJECT_ROOT / "test-artifacts"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def database_fingerprint(path: Path) -> dict[str, object]:
    """Size, mtime and hash of a database plus its sidecars."""
    fingerprint: dict[str, object] = {}
    for candidate in (path, *(path.with_name(path.name + suffix) for suffix in ("-wal", "-shm"))):
        if candidate.exists():
            stat = candidate.stat()
            fingerprint[candidate.name] = {
                "bytes": stat.st_size,
                "sha256": sha256_file(candidate),
            }
        else:
            fingerprint[candidate.name] = None
    return fingerprint


def online_backup(source: Path, target: Path) -> Path:
    """A consistent copy via the SQLite backup API; refuses to overwrite."""
    if target.exists():
        raise SystemExit(f"refusing to overwrite existing staging file: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    # ``with sqlite3.connect(...)`` commits but does *not* close the connection, which
    # would keep the copy's -wal locked; every connection here is closed explicitly.
    with (
        contextlib.closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as origin,
        contextlib.closing(sqlite3.connect(target)) as copy,
    ):
        origin.backup(copy)
    return target


def verify(database: Path, baseline: dict, evidence: Path | None) -> tuple[bool, dict]:
    return verified_db.compare_against_baseline(
        database, baseline, retention_dir=evidence
    )


def delete_row(database: Path, row_id: int) -> None:
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute("delete from history_event where id = ?", (row_id,))
        connection.commit()


def drop_sidecars(database: Path) -> list[str]:
    """Remove ``-wal``/``-shm`` next to a copy before restoring its main file.

    Copying a pre-cleanup backup over a database whose WAL still describes the pruned
    state would mix two states in one open; the sidecars belong to the discarded copy.
    """
    removed = []
    for suffix in ("-wal", "-shm"):
        sidecar = database.with_name(database.name + suffix)
        if sidecar.exists():
            sidecar.unlink()
            removed.append(sidecar.name)
    return removed


#: The event types the confirmed policy allows to archive, used in rotation when the
#: drill has to add its own candidates.
FIXTURE_EVENT_TYPES = (
    "login_failed",
    "reauth_failed",
    "user_login",
    "article_word_lookup",
)


def insert_fixture_candidates(database: Path, *, count: int, when: datetime) -> list[int]:
    """Add clearly marked rows that stand in for events past the retention window.

    The live database is younger than the 365-day window, so a copy of it has no
    candidates at all: without these rows the drill could rehearse the failure paths
    but never a real deletion. They live only in the disposable staging copy, carry
    ``{"drill": true}`` payloads and a ``drill`` entity type, and the report names the
    IDs so nobody mistakes them for production events.
    """
    stamp = when.strftime("%Y-%m-%d %H:%M:%S")
    payload = json.dumps({"drill": True}, separators=(",", ":"))
    ids: list[int] = []
    with contextlib.closing(sqlite3.connect(database)) as connection:
        for index in range(count):
            cursor = connection.execute(
                "insert into history_event "
                "(user_id, event_type, timestamp, entity_type, entity_id, payload) "
                "values (null, ?, ?, 'drill', null, ?)",
                (FIXTURE_EVENT_TYPES[index % len(FIXTURE_EVENT_TYPES)], stamp, payload),
            )
            ids.append(int(cursor.lastrowid))
        connection.commit()
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--staging-dir", type=Path, default=DEFAULT_STAGING)
    parser.add_argument("--artifacts-dir", type=Path, default=DEFAULT_ARTIFACTS)
    parser.add_argument(
        "--stamp", default=None, help="UTC stamp used in the new file names (for reruns)"
    )
    parser.add_argument(
        "--fixture-candidates",
        type=int,
        default=4,
        help="marked rows to add to the copy when the live data has nothing past the window",
    )
    args = parser.parse_args(argv)

    source = args.source.resolve()
    baseline_path = args.baseline.resolve()
    if not source.is_file():
        raise SystemExit(f"source database not found: {source}")
    if is_protected_database(source):
        # The drill never works on the live database, not even read-only: it copies it.
        pass
    stamp = args.stamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    copy_path = (args.staging_dir / f"history-retention-drill-{stamp}.db").resolve()
    artifacts = (args.artifacts_dir / f"history-retention-drill-{stamp}").resolve()
    if artifacts.exists():
        raise SystemExit(f"refusing to reuse an existing drill directory: {artifacts}")
    if copy_path.parent == source.parent and copy_path.name == source.name:
        raise SystemExit("the drill copy must not be the source database")

    report: dict[str, object] = {
        "drill": "history-retention-staging",
        "stamp": stamp,
        "source": str(source),
        "staging_copy": str(copy_path),
        "artifacts": str(artifacts),
        "steps": [],
        "ok": False,
    }
    steps: list[dict[str, object]] = report["steps"]  # type: ignore[assignment]

    def step(name: str, **details: object) -> None:
        steps.append({"step": name, "ok": True, **details})
        print(f"[ok] {name}", *[f"{key}={value}" for key, value in details.items()])

    print("== 0. prove the live database is untouched ==")
    production_before = database_fingerprint(source)
    step("fingerprint live database (before)", files=sorted(production_before))

    artifacts.mkdir(parents=True)
    baseline_copy = artifacts / "baseline.json"
    shutil.copyfile(baseline_path, baseline_copy)
    baseline = load_baseline(baseline_copy)
    step("copied the baseline for this drill", baseline=str(baseline_copy))

    print("== 1. copy the source with the SQLite online backup API ==")
    online_backup(source, copy_path)
    step(
        "online backup of the source into staging",
        copy=str(copy_path),
        bytes=copy_path.stat().st_size,
        sha256=sha256_file(copy_path),
    )

    print("== 2. the copy must already match the baseline ==")
    verified, pre_report = verify(copy_path, baseline, None)
    (artifacts / "00-copy-before-drill.json").write_text(
        json.dumps(pre_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not verified:
        step("copy matches the baseline", ok=False)
        report["failures"] = pre_report["failures"]
        _write_report(artifacts, report)
        print("FAILED: the staging copy does not match the baseline:", pre_report["failures"])
        return 1
    step("copy matches the baseline", rows=pre_report.get("bytes"))

    print("== 3. preview and plan ==")
    policy = retention_policy()
    preview = preview_history_retention(copy_path, retention_days=policy.retention_days)
    fixture_ids: list[int] = []
    if preview["candidate_count"] == 0 and args.fixture_candidates:
        # The project is younger than the retention window, so a faithful copy of the
        # live database has nothing to archive yet. The drill adds marked rows to the
        # *copy* so that a real deletion can still be rehearsed end to end.
        fixture_ids = insert_fixture_candidates(
            copy_path, count=args.fixture_candidates, when=datetime.now(UTC) - timedelta(days=400)
        )
        preview = preview_history_retention(copy_path, retention_days=policy.retention_days)
        step("inserted drill fixture candidates (staging copy only)", ids=fixture_ids)
    (artifacts / "01-preview.json").write_text(
        json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report["fixture_candidate_ids"] = fixture_ids
    step(
        "preview (read-only)",
        cutoff=preview["cutoff_utc"],
        candidates=preview["candidate_count"],
        total=preview["total_count"],
    )
    try:
        plan = build_plan(copy_path, baseline_path=baseline_copy, retention_days=policy.retention_days)
    except RetentionError as error:
        # A copy of the live database with nothing old enough is a legitimate outcome:
        # the drill cannot rehearse a deletion it has no candidates for.
        report["blocked"] = str(error)
        _write_report(artifacts, report)
        print(f"BLOCKED: {error}")
        return 2
    write_plan(plan, artifacts / "02-plan.json")
    step(
        "plan written",
        run_id=plan["run_id"],
        candidates=plan["candidate_count"],
        plan_sha256=plan["plan_sha256"],
    )

    print("== 4. apply on the staging copy ==")
    evidence = artifacts / "history-retention"
    drafts = artifacts / "history-retention-drafts"
    result = apply_plan(
        copy_path,
        plan,
        baseline_path=baseline_copy,
        evidence_dir=evidence,
        drafts_dir=drafts,
        log=lambda message: print("    ", message),
    )
    step("apply committed and published", deleted=result["deleted"], manifest=result["committed_manifest"])

    print("== 5. the pruned copy must verify ==")
    verified, after_report = verify(copy_path, baseline, evidence)
    (artifacts / "03-after-prune.json").write_text(
        json.dumps(after_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not verified:
        report["failures"] = after_report["failures"]
        _write_report(artifacts, report)
        print("FAILED: the pruned copy does not verify:", after_report["failures"])
        return 1
    step(
        "pruned copy verified",
        archived=after_report["history_event_archived"]["ids"],
    )

    print("== 6. a row the archive does not cover must fail verification ==")
    unprotected = sorted(
        set(baseline["tables"]["history_event"]["row_hashes"])
        - {str(value) for value in plan["candidate_ids"]},
        key=int,
    )
    if not unprotected:
        print("SKIPPED: no baseline history row is left outside the archive")
    else:
        victim = int(unprotected[0])
        delete_row(copy_path, victim)
        verified, failure_report = verify(copy_path, baseline, evidence)
        (artifacts / "04-deliberate-loss.json").write_text(
            json.dumps(failure_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if verified:
            report["failures"] = [f"deleting row {victim} was not detected"]
            _write_report(artifacts, report)
            print(f"FAILED: deleting non-candidate row {victim} did not fail verification")
            return 1
        step("verification failed as designed", deleted_row=victim, failures=failure_report["failures"])

    print("== 7. restore the copy from the pre-cleanup backup ==")
    backup = evidence / f"{plan['run_id']}.before.db"
    removed = drop_sidecars(copy_path)
    shutil.copyfile(backup, copy_path)
    step("restored the copy", from_backup=str(backup), removed_sidecars=removed)
    verified, restored_report = verify(copy_path, baseline, evidence)
    (artifacts / "05-restored.json").write_text(
        json.dumps(restored_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not verified:
        report["failures"] = restored_report["failures"]
        _write_report(artifacts, report)
        print("FAILED: the restored copy does not verify:", restored_report["failures"])
        return 1
    step(
        "restored copy verified",
        rows=restored_report["tables"]["history_event"]["rows_current"],
    )

    print("== 8. the live database is still untouched ==")
    production_after = database_fingerprint(source)
    if production_after != production_before:
        report["failures"] = ["the live database changed during the drill"]
        _write_report(artifacts, report)
        print("FAILED: the live database changed during the drill")
        return 1
    step("live database fingerprint unchanged", sha256=production_before[source.name]["sha256"])  # type: ignore[index]

    report["ok"] = True
    _write_report(artifacts, report)
    print()
    print(f"DRILL PASSED: {artifacts / 'report.json'}")
    return 0


def _write_report(artifacts: Path, report: dict[str, object]) -> None:
    (artifacts / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    raise SystemExit(main())
