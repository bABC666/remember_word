"""Build a new synthetic database from committed, fingerprinted public inputs."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import make_engine
from app.models import (
    EntrySourceEvidence,
    HistoryEvent,
    Lexicon,
    LexiconEntry,
    PublicImportRun,
    PublicImportRunSource,
    SourceArtifact,
    User,
    UserSettings,
)
from app.security import UNUSABLE_PASSWORD, hash_password
from app.services.public_lexicon_confirm import confirm_plan
from app.services.public_lexicon_plan import build_plan
from app.services.source_attribution import validate_attribution

BUNDLE_SHA256 = "c59f8a12f00a15c4dcf508d01aacc261ff80fa03ce40663e26c42214937ac296"
BACKEND = Path(__file__).resolve().parents[1]


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_bundle(bundle: Path) -> str:
    if bundle.is_symlink() or getattr(bundle, "is_junction", lambda: False)():
        raise ValueError("public package link refused")
    fp = json.loads((bundle / "fingerprints.json").read_text("utf-8"))
    # The source-controlled constant is the authority, not the package's own claim.
    expected = hashlib.sha256(json.dumps(fp["files"], sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    if fp["package_sha256"] != expected or expected != BUNDLE_SHA256:
        raise ValueError("public package authority mismatch")
    actual = {}
    for path in bundle.rglob("*"):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("public package link refused")
        if path.is_file() and path.name != "fingerprints.json":
            actual[path.relative_to(bundle).as_posix()] = sha(path)
    if actual != fp["files"]:
        raise ValueError("public package bytes or members changed")
    return expected


def _append_gap(session: Session, lexicon: Lexicon, admin: User, bundle: Path) -> None:
    choices = json.loads((bundle / "gap/choices.json").read_text("utf-8"))
    sources = json.loads((bundle / "gap/sources.json").read_text("utf-8"))
    if len(choices) != 77 or len(sources) != 77:
        raise ValueError("77 fixed public decisions required")
    moment = datetime.now(UTC)
    run = PublicImportRun(
        plan_sha256=digest({"classmate_gap": BUNDLE_SHA256}),
        run_id="classmate-gap-" + BUNDLE_SHA256[:32], target_lexicon_id=lexicon.id,
        confirmed_by_user_id=admin.id, confirmed_by_username="admin",
        confirmed_at=moment, status="applied", entries_created=0, entries_matched=77,
        evidence_written=77,
        result_json={"kind": "classmate-public-gap-seed", "human_reviewed": False,
                     "package_sha256": BUNDLE_SHA256},
    )
    session.add(run)
    session.flush()
    for row, source in zip(choices, sources, strict=True):
        entry = session.scalar(select(LexiconEntry).where(
            LexiconEntry.lexicon_id == lexicon.id, LexiconEntry.word == row["word"],
            LexiconEntry.sequence == row["sequence"]))
        if entry is None or entry.source_meanings != []:
            raise ValueError("public empty-word identity mismatch")
        file = bundle / "gap" / source["file"]
        with file.open(encoding="utf-8", newline="") as handle:
            data = list(csv.DictReader(handle))
        if len(data) != 1 or data[0]["word"] != row["word"] or (
            [data[0]["meaning"]] != row["new_meanings"] or data[0]["oldid"] != row["oldid"]
        ):
            raise ValueError("fixed public decision does not match its CSV")
        mapping = source["mapping"]
        validate_attribution(mapping["attribution"])
        artifact = SourceArtifact(
            role="supplement", name=source["file"], publisher=source["publisher"],
            version=source["version"], obtained_at_utc=source["obtained_at_utc"],
            format="delimited-text-v1", mapping_json=canonical(mapping).decode(),
            mapping_sha256=digest(mapping), file_sha256=sha(file), byte_size=file.stat().st_size,
            license_id=source["license_id"], license_text_sha256=source["license_text_sha256"],
            use_scope=source["use_scope"], display_scope=source["display_scope"],
            storage_locator="committed-public:gap/" + source["file"],
        )
        session.add(artifact)
        session.flush()
        session.add(PublicImportRunSource(import_run_id=run.id, source_artifact_id=artifact.id,
                                         outcome="created", detail=source["kind"]))
        session.add(EntrySourceEvidence(
            lexicon_entry_id=entry.id, source_artifact_id=artifact.id, import_run_id=run.id,
            normalized_word=entry.normalized_word, row_locator=2, field_kind="meaning",
            sense_key=source["sense_key"], raw_word=row["word"], raw_text=data[0]["meaning"],
            evidence_sha256=digest({"package": BUNDLE_SHA256, "word": row["word"]}),
            decision="selected", selected_for_default=True, selection_order=1,
            source_revision=row["oldid"], confirmed_by_username="admin", confirmed_at=moment,
        ))
        entry.source_meanings = row["new_meanings"]
    admin.is_active = False
    session.add(User(username="classmate_test", display_name="同学测试", role="user",
                     password_hash=hash_password("Shici-Test-2026!")))
    session.flush()
    learner = session.scalar(select(User).where(User.username == "classmate_test"))
    session.add(UserSettings(user_id=learner.id))
    # The new public import emits seed-operation history. No learner has existed
    # until this transaction; keep provenance in public_import_run, omit logs.
    session.execute(delete(HistoryEvent))
    session.commit()


def build_database(database: Path, bundle: Path) -> dict:
    verify_bundle(bundle)
    if database.name != "classmate.sqlite3" or database.parent.name != "classmate-test":
        raise ValueError("only the fixed classmate-test database is accepted")
    if database.exists():
        raise ValueError("existing database must be retained; use explicit Reset")
    database.parent.mkdir(parents=True, exist_ok=True)
    staging = database.with_name("classmate.building.sqlite3")
    # O_EXCL prevents two builders from sharing a partial database or following a link.
    fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    engine = None
    try:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("VOCAB_", "DEEPSEEK_"))}
        env.update(VOCAB_BOOTSTRAP_USERNAME="admin", VOCAB_DATA_DIR=str(database.parent),
                   VOCAB_DATABASE_PATH=str(staging), VOCAB_ENABLE_OCR="false", PYTHONUTF8="1")
        subprocess.run([sys.executable, "-m", "alembic", "-c", str(BACKEND / "alembic.ini"),
                        "-x", "db_url=sqlite:///" + staging.as_posix(), "upgrade", "head"],
                       cwd=BACKEND, env=env, check=True)
        engine = make_engine("sqlite:///" + staging.as_posix())
        base = bundle / "base"
        plan = build_plan(manifest_path=Path("manifest.json"), source_root=base,
                          decisions_path=Path("decisions.json"), target_lexicon="NETEM")
        if not plan["confirmation_ready"] or plan["summary"]["ready_entries"] != 5528:
            raise ValueError("fixed base plan is not ready")
        with Session(engine) as session:
            admin = session.scalar(select(User).where(User.username == "admin"))
            if admin.password_hash != UNUSABLE_PASSWORD:
                raise ValueError("migration bootstrap must not have a usable password")
            lexicon = Lexicon(name="NETEM", visibility="public", source_type="netem",
                              description="固定公共测试库；独立来源和许可见来源详情")
            session.add(lexicon)
            session.flush()
            confirm_plan(session, plan=plan, administrator=admin, source_root=base)
            _append_gap(session, lexicon, admin, bundle)
        engine.dispose()
        engine = None
        import sqlite3
        with closing(sqlite3.connect(staging)) as db:
            words = db.execute("select word,sequence,source_meanings from lexicon_entry "
                               "order by sequence").fetchall()
            evidence = db.execute("select count(*) from entry_source_evidence").fetchone()[0]
            empty = sum(not json.loads(r[2]) for r in words)
            if len(words) != 5528 or empty or evidence != 16507:
                raise ValueError("synthetic content count mismatch")
            if db.execute("pragma integrity_check").fetchone()[0] != "ok" or (
                db.execute("pragma foreign_key_check").fetchall()
            ):
                raise ValueError("synthetic database integrity failure")
        # Publish without overwriting a database created by another process.
        os.link(staging, database)
        staging.unlink()
        return {"package_sha256": BUNDLE_SHA256, "words": 5528, "empty": 0,
                "evidence": evidence, "public_content_sha256": digest(words),
                "generated_database_sha256": sha(database)}
    finally:
        if engine is not None:
            engine.dispose()
        if staging.exists():
            staging.unlink()
