"""Locked NETEM empty-only updates, with append-only audit and local retraction.

This is separate from the insert-only public confirmation service. It never creates
a lexicon/entry, a concise meaning, a user state, or a migration. A candidate digest
must be supplied by a trusted release index, not by the package itself.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    EntrySourceEvidence,
    PublicImportRun,
    PublicImportRunSource,
    SourceArtifact,
    User,
)
from app.services.source_attribution import validate_attribution

ROOT = Path(__file__).resolve().parents[3]
BASELINE_SHA = "0420923f302b40166b23ae56c2652e93b8d83f3d418fc52020dd6427caf776ab"
INITIAL_PLAN = "5e17f98651acbb5df9af697c6a19b6abbd0088e7373cce99603059e2b21497fe"
KIND = "netem-gap-update-v1"
RETRACTION_KIND = "netem-gap-retraction-v1"


class GapRefused(ValueError):
    """A mismatch refused the entire operation; no partial changes are committed."""


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise GapRefused(message)


def plan_digest(plan) -> str:
    return digest({k: v for k, v in plan.items() if k != "plan_sha256"})


def verify_candidate(candidate: Path, expected_sha: str) -> tuple[list, list]:
    candidate = candidate.resolve()
    fp = json.loads((candidate / "fingerprints.json").read_text("utf8"))
    require(fp["baseline_sha256"] == BASELINE_SHA, "wrong frozen baseline")
    require(fp["package_sha256"] == expected_sha == digest(fp["files"]), "candidate authority mismatch")
    actual = {p.relative_to(candidate).as_posix(): file_sha(p) for p in candidate.rglob("*")
              if p.is_file() and p.name != "fingerprints.json"}
    require(actual == fp["files"], "candidate bytes/member mismatch")
    require(all((candidate / name).resolve().is_relative_to(candidate) for name in actual),
            "candidate paths escape archive")
    baseline = ROOT / "test-artifacts/netem-final-20261005/frozen"
    bfp = json.loads((baseline / "fingerprints.json").read_text("utf8"))
    require(bfp["package_sha256"] == BASELINE_SHA, "original frozen package replaced")
    require(all(file_sha(baseline / name) == value for name, value in bfp["files"].items()),
            "original frozen bytes changed")
    original = list(csv.DictReader((baseline / "candidate-provenance.csv").open(encoding="utf8")))
    ledger = json.loads((candidate / "ledger.json").read_text("utf8"))
    sources = json.loads((candidate / "sources.json").read_text("utf8"))
    require([(r["word"], r["sequence"]) for r in ledger] ==
            [(r["word"], int(r["sequence"])) for r in original if not r["meaning"]],
            "ledger must be the exact frozen 77-empty list in original order")
    require(len(ledger) == len(sources) == 77, "expected 77 decisions and sources")
    require(len({r["entry_id"] for r in ledger}) == 77, "duplicate entry identity")
    pages = json.loads((candidate / "snapshots/pages.json").read_text("utf8"))
    for row, source in zip(ledger, sources, strict=True):
        require(row["source_file"] == source["file"] and row["original_meanings"] == [],
                "source or expected empty value mismatch")
        require(row["human_reviewed"] is False and row["self_authored_supplement"] is False,
                "not a human-confirmed concise meaning")
        validate_attribution(source["mapping"]["attribution"])
        page = pages[row["source_title"]]
        require(hashlib.sha256(page["text"].encode()).hexdigest() == row["page_text_sha256"] ==
                page["text_sha256"], "source page text mismatch")
        require(str(page["oldid"]) == row["oldid"], "source revision mismatch")
        for citation in row["citations"]:
            require(page["text"].splitlines()[citation["line"] - 1] == citation["raw_text"],
                    "cited source line mismatch")
        for relation in row["form_relation"]:
            form_page = pages[relation["title"]]
            require(str(form_page["oldid"]) == str(relation["oldid"]) and
                    form_page["text_sha256"] == relation["page_text_sha256"], "word-form revision mismatch")
            for citation in relation["lines"]:
                prefix = form_page["text"].splitlines()[:citation["line"]]
                languages = [line for line in prefix if re.match(r"^==[^=].*[^=]==$", line)]
                require(languages[-1:] == ["==English=="] and prefix[-1] == citation["raw_text"],
                        "word-form relation must cite the exact English section")
        with (candidate / source["file"]).open(encoding="utf8", newline="") as handle:
            content = list(csv.DictReader(handle))
        require(len(content) == 1 and content[0]["word"] == row["word"] and
                [content[0]["meaning"]] == row["new_meanings"] and
                content[0]["oldid"] == row["oldid"], "CSV cannot reproduce chosen meaning")
        require(bool(content[0]["meaning"].strip()), "empty new meaning")
    return ledger, sources


def _query(connection, sql, parameters=()) -> list:
    if isinstance(connection, sqlite3.Connection):
        return connection.execute(sql, parameters).fetchall()
    return [tuple(r) for r in connection.exec_driver_sql(sql, parameters).all()]


def _identity(connection):
    require(_query(connection, "select version_num from alembic_version") ==
            [("0015_session_autoincrement",)], "wrong database revision")
    require(_query(connection, "select id,name,owner_user_id,source_type,visibility from lexicon where id=5") ==
            [(5, "NETEM", None, "netem", "public")], "target identity mismatch")
    require(_query(connection, "select id from lexicon where name='NETEM' and owner_user_id is null") ==
            [(5,)], "ambiguous NETEM target")
    require(_query(connection, "select plan_sha256,target_lexicon_id,entries_created,evidence_written from public_import_run where id=1") ==
            [(INITIAL_PLAN, 5, 5528, 16430)], "initial import identity mismatch")


def _baseline(connection) -> dict:
    _identity(connection)
    entries = _query(connection, "select * from lexicon_entry where lexicon_id=5 order by sequence")
    require(len(entries) == 5528, "NETEM membership changed")
    evidence = _query(connection, "select * from entry_source_evidence where import_run_id=1 order by id")
    require(len(evidence) == 16430, "original evidence changed")
    return {"entry_hashes": {str(r[0]): digest(r) for r in entries},
            "initial_evidence_sha256": digest(evidence),
            "initial_run_sha256": digest(_query(connection, "select * from public_import_run where id=1")),
            "initial_artifacts_sha256": digest(_query(connection,
                "select * from source_artifact where id in (select source_artifact_id from public_import_run_source where import_run_id=1) order by id")),
            "initial_links_sha256": digest(_query(connection,
                "select * from public_import_run_source where import_run_id=1 order by id"))}


def _require_frozen_contents(connection):
    with (ROOT / "test-artifacts/netem-final-20261005/frozen/candidate-provenance.csv").open(encoding="utf8") as handle:
        original = list(csv.DictReader(handle))
    stored = _query(connection, "select word,sequence,source_meanings from lexicon_entry where lexicon_id=5 order by sequence")
    require([(r[0], r[1], json.loads(r[2])) for r in stored] ==
            [(r["word"], int(r["sequence"]), [r["meaning"]] if r["meaning"] else []) for r in original],
            "current NETEM content differs from the frozen first launch")


def build_gap_plan(database: Path, *, candidate: Path, expected_sha: str) -> dict:
    ledger, _ = verify_candidate(candidate, expected_sha)
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = None
        plan = {"type": KIND, "candidate_sha256": expected_sha, "baseline_sha256": BASELINE_SHA,
                "target_lexicon_id": 5, "initial_import_run_id": 1, "baseline": _baseline(connection),
                "changes": []}
        _require_frozen_contents(connection)
        for row in ledger:
            before = _query(connection, "select word,normalized_word,sequence,source_meanings,updated_at from lexicon_entry where id=? and lexicon_id=5", (row["entry_id"],))
            require(len(before) == 1 and before[0][:3] == (row["word"], row["word"].casefold(), row["sequence"]),
                    "candidate entry identity differs from target")
            require(json.loads(before[0][3]) == [], "only exact current empty meanings may change")
            plan["changes"].append({"entry_id": row["entry_id"], "word": row["word"],
                "sequence": row["sequence"], "old_meanings_json": before[0][3],
                "old_updated_at": before[0][4], "new_meanings": row["new_meanings"],
                "source_file": row["source_file"], "source_revision": row["oldid"]})
    plan["plan_sha256"] = plan_digest(plan)
    plan["run_id"] = "netem-gap-" + plan["plan_sha256"][:24]
    # run_id is deterministic but is also covered, so no operator can relabel a plan.
    plan["plan_sha256"] = plan_digest(plan)
    return plan


def _write_meaning(connection, row, value, updated_at, expected_json):
    changed = connection.exec_driver_sql(
        "update lexicon_entry set source_meanings=?,updated_at=? where id=? and lexicon_id=5 "
        "and word=? and normalized_word=? and sequence=? and source_meanings=?",
        (value, updated_at, row["entry_id"], row["word"], row["word"].casefold(),
         row["sequence"], expected_json),
    ).rowcount
    require(changed == 1, "expected identity/original value no longer matches")


def confirm_gap(session: Session, *, plan: dict, administrator: User,
                candidate: Path, expected_sha: str, action: str = "apply") -> dict:
    """Atomic compare-and-set; retract checks only this slice's changed values.

    Retraction appends a run, retains evidence and restores the two columns this
    update changed. Read paths mark that run's evidence as retracted. New user data
    and other entry columns are never restored from a historical whole database.
    """
    require(action in {"apply", "retract"}, "unknown action")
    ledger, sources = verify_candidate(candidate, expected_sha)
    require(plan.get("type") == KIND and plan.get("candidate_sha256") == expected_sha and
            plan.get("baseline_sha256") == BASELINE_SHA and plan.get("target_lexicon_id") == 5 and
            plan.get("initial_import_run_id") == 1 and
            plan.get("plan_sha256") == plan_digest(plan), "locked plan digest/type mismatch")
    expected_changes = [(r["entry_id"], r["word"], r["sequence"], r["new_meanings"],
                         r["source_file"], r["oldid"]) for r in ledger]
    require([(r["entry_id"], r["word"], r["sequence"], r["new_meanings"],
              r["source_file"], r["source_revision"]) for r in plan["changes"]] ==
            expected_changes, "plan contents do not reproduce trusted candidate")
    require(all(json.loads(r["old_meanings_json"]) == [] for r in plan["changes"]),
            "plan does not expect exact empty meanings")
    require(not session.new and not session.dirty and not session.deleted, "pending unrelated writes")
    try:
        connection = session.connection()
        connection.exec_driver_sql("pragma foreign_keys=on")
        connection.exec_driver_sql("begin immediate")
        admin = session.scalar(select(User).where(User.id == getattr(administrator, "id", None)))
        require(admin is not None and admin.is_active and admin.role == "admin", "active administrator required")
        current_admin = _query(connection, "select username,role,is_active,password_hash from user where id=?", (admin.id,))
        require(current_admin == [(admin.username, "admin", 1, administrator.password_hash)],
                "administrator changed since authentication")
        _identity(connection)
        existing = session.scalar(select(PublicImportRun).where(PublicImportRun.plan_sha256 == plan["plan_sha256"]))
        revert_sha = digest({"type": RETRACTION_KIND, "update_plan_sha256": plan["plan_sha256"]})
        retraction = session.scalar(select(PublicImportRun).where(PublicImportRun.plan_sha256 == revert_sha))
        if retraction is not None:
            require(existing is not None and retraction.target_lexicon_id == existing.target_lexicon_id == 5 and
                    retraction.result_json.get("retracted_run_id") == existing.id, "retraction identity mismatch")
            require(action == "retract", "same update plan has been retracted; cannot reapply")
            session.rollback()
            return {"status": "already_retracted", "import_run_id": retraction.id}
        if existing is not None:
            require(existing.target_lexicon_id == 5 and existing.result_json.get("kind") == KIND and
                    existing.result_json.get("locked_plan") == plan, "previous run identity mismatch")
            writes = existing.result_json["writes"]
            for row in writes:
                current = _query(connection, "select word,sequence,source_meanings,updated_at from lexicon_entry where id=? and lexicon_id=5", (row["entry_id"],))
                require(current == [(row["word"], row["sequence"], row["new_meanings_json"], row["new_updated_at"])],
                        "applied value changed; cannot acknowledge/retract it")
            if action == "apply":
                session.rollback()
                return {"status": "already_applied", "import_run_id": existing.id, "entries_updated": 0}
            moment = datetime.now(UTC)
            for row in writes:
                _write_meaning(connection, row, row["old_meanings_json"], row["old_updated_at"], row["new_meanings_json"])
            run = PublicImportRun(plan_sha256=revert_sha, run_id="retract-" + plan["run_id"],
                target_lexicon_id=5, confirmed_by_user_id=admin.id, confirmed_by_username=admin.username,
                confirmed_at=moment, status="applied", entries_created=0, entries_matched=len(writes),
                evidence_written=0, result_json={"kind": RETRACTION_KIND, "retracted_run_id": existing.id,
                    "update_plan_sha256": plan["plan_sha256"], "restored_entry_ids": [r["entry_id"] for r in writes]})
            session.add(run)
            session.flush()
            result = {"status": "retracted", "import_run_id": run.id, "entries_updated": len(writes)}
        else:
            require(action == "apply", "update has not been applied")
            _require_frozen_contents(connection)
            require(_baseline(connection) == plan["baseline"], "entry/evidence/import baseline mismatch")
            for row in plan["changes"]:
                current = _query(connection, "select source_meanings,updated_at from lexicon_entry where id=? and lexicon_id=5", (row["entry_id"],))
                require(current == [(row["old_meanings_json"], row["old_updated_at"])],
                        "expected original columns do not match")
            moment = datetime.now(UTC)
            stamp = moment.strftime("%Y-%m-%d %H:%M:%S.%f")
            writes = [{**r, "new_meanings_json": json.dumps(r["new_meanings"], ensure_ascii=False),
                       "new_updated_at": stamp} for r in plan["changes"]]
            run = PublicImportRun(plan_sha256=plan["plan_sha256"], run_id=plan["run_id"],
                target_lexicon_id=5, confirmed_by_user_id=admin.id, confirmed_by_username=admin.username,
                confirmed_at=moment, status="applied", entries_created=0, entries_matched=77,
                evidence_written=77, result_json={"kind": KIND, "candidate_sha256": expected_sha,
                    "locked_plan": plan, "writes": writes, "human_reviewed": False})
            session.add(run)
            session.flush()
            for row, source in zip(writes, sources, strict=True):
                mapping = source["mapping"]
                artifact = SourceArtifact(role="supplement", name=source["file"], publisher=source["publisher"],
                    version=source["version"], obtained_at_utc=source["obtained_at_utc"],
                    format="delimited-text-v1", mapping_json=canonical(mapping).decode(),
                    mapping_sha256=digest(mapping), file_sha256=file_sha(candidate / source["file"]),
                    byte_size=(candidate / source["file"]).stat().st_size, license_id=source["license_id"],
                    license_text_sha256=source["license_text_sha256"], use_scope=source["use_scope"],
                    display_scope=source["display_scope"], storage_locator="candidate:" + source["file"])
                session.add(artifact)
                session.flush()
                session.add(PublicImportRunSource(import_run_id=run.id, source_artifact_id=artifact.id,
                    outcome="created", detail="NETEM empty-only补义；" + source["kind"]))
                session.add(EntrySourceEvidence(lexicon_entry_id=row["entry_id"], source_artifact_id=artifact.id,
                    import_run_id=run.id, normalized_word=row["word"].casefold(), row_locator=2,
                    field_kind="meaning", sense_key=source["sense_key"], raw_word=row["word"],
                    raw_text=row["new_meanings"][0], evidence_sha256=digest({"candidate": expected_sha, "word": row["word"]}),
                    decision="selected", selected_for_default=True, selection_order=1,
                    source_revision=row["source_revision"], confirmed_by_username=admin.username,
                    confirmed_at=moment))
                _write_meaning(connection, row, row["new_meanings_json"], stamp, row["old_meanings_json"])
            result = {"status": "applied", "import_run_id": run.id, "entries_updated": 77,
                      "sources_created": 77, "evidence_written": 77, "plan_sha256": plan["plan_sha256"]}
        session.commit()
        return result
    except BaseException:
        session.rollback()
        raise
