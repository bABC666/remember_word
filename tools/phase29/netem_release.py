"""Explicit frozen NETEM import tooling; production writes require the CLI flag.

Rehearsal calls the same atomic confirmation function on guarded copies.
No migration, user enrolment, overwrite or withdrawal is performed here.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
PRODUCTION = (ROOT / "data/vocab.db").resolve()
FROZEN = ROOT / "test-artifacts/netem-final-20261005/frozen"
CANDIDATE_SHA = "0420923f302b40166b23ae56c2652e93b8d83f3d418fc52020dd6427caf776ab"
REVISION = "0015_session_autoincrement"
if sys.flags.optimize:
    raise RuntimeError("release verification requires Python assertions; do not use -O")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_package() -> None:
    index = json.loads((ROOT / "docs/NETEM-FINAL-CANDIDATE-EVIDENCE-2026-10-05.json").read_text("utf-8"))
    assert index["candidate"]["package_sha256"] == CANDIDATE_SHA
    fp = json.loads((FROZEN / "fingerprints.json").read_text("utf-8"))
    assert fp["package_sha256"] == CANDIDATE_SHA
    assert sha(FROZEN / "fingerprints.json") == index["files"]["test-artifacts/netem-final-20261005/frozen/fingerprints.json"]
    actual = {p.relative_to(FROZEN).as_posix(): sha(p) for p in sorted(FROZEN.rglob("*"))
              if p.is_file() and p.name != "fingerprints.json"}
    assert actual == fp["files"], "frozen candidate changed"


def guard_database(database: Path, *, production: bool = False) -> Path:
    path = database.resolve()
    if production:
        if path != PRODUCTION:
            raise ValueError("production import accepts only the fixed production path")
    elif not path.is_relative_to((ROOT / "test-artifacts").resolve()) or path == PRODUCTION:
        raise ValueError("copy writes accept only databases inside test-artifacts")
    if not path.is_file():
        raise ValueError("existing database required; no implicit initialisation")
    if not production and path.samefile(PRODUCTION):
        raise ValueError("a production hard link is not an isolated copy")
    return path


@contextmanager
def readonly(database: Path):
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        yield connection
    finally:
        connection.close()


def verify_revision(database: Path) -> None:
    from app.db import code_head_revision
    assert code_head_revision() == REVISION
    with readonly(database) as con:
        assert con.execute("select version_num from alembic_version").fetchall() == [(REVISION,)]
        assert con.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert con.execute("pragma foreign_key_check").fetchall() == []


def engine_for(database: Path):
    engine = create_engine("sqlite:///" + database.as_posix())

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("pragma foreign_keys=on")
    return engine


def build_final_plan() -> dict:
    from app.services.public_lexicon_plan import build_plan
    verify_package()
    plan = build_plan(manifest_path=Path("manifest.json"), source_root=FROZEN / "public-package",
                      decisions_path=Path("decisions.json"), target_lexicon="NETEM")
    assert plan["confirmation_ready"] and plan["summary"]["ready_entries"] == 5528
    return plan


def atomic_confirm(session: Session, *, plan: dict, administrator) -> dict:
    from app.models import Lexicon, PublicImportRun
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan
    verify_package()
    expected = build_final_plan()
    if plan["plan_sha256"] != expected["plan_sha256"]:
        raise ConfirmRefused("plan is not the unique frozen NETEM plan")
    targets = session.scalars(select(Lexicon).where(Lexicon.name == "NETEM",
        Lexicon.owner_user_id.is_(None))).all()
    if len(targets) > 1 or (targets and (targets[0].source_type != "netem" or targets[0].visibility != "public")):
        raise ConfirmRefused("ambiguous or unrelated public NETEM target")
    if targets and session.scalar(select(PublicImportRun).where(
        PublicImportRun.target_lexicon_id == targets[0].id,
        PublicImportRun.plan_sha256 == plan["plan_sha256"],
    )) is None:
        raise ConfirmRefused("existing NETEM has no matching final import run; inspect it before retrying")
    if not targets:
        # This row and confirmation content commit together; a failure leaves no
        # empty public library that could become a premature recommendation.
        session.add(Lexicon(name="NETEM", visibility="public", source_type="netem",
                            description="NETEM 默认词库；来源与许可见对应来源详情"))
        session.flush()
    try:
        return confirm_plan(session, plan=plan, administrator=administrator,
                            source_root=FROZEN / "public-package")
    except BaseException:
        session.rollback()
        raise


def check_code(expected: str) -> None:
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True).strip()
    if actual != expected or dirty:
        raise ValueError("code SHA not locked or tracked worktree dirty")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["plan", "import"])
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--admin")
    parser.add_argument("--confirm")
    parser.add_argument("--code-sha", required=True)
    parser.add_argument("--production-import", action="store_true")
    args = parser.parse_args()
    check_code(args.code_sha)
    if args.action == "plan":
        if args.plan.exists():
            raise ValueError("refusing to overwrite a locked plan")
        write_json(args.plan, build_final_plan())
        return
    from app.models import User
    from app.services.auth import verify_user_password
    from app.services.public_lexicon_plan import load_plan
    database = guard_database(args.database, production=args.production_import)
    verify_revision(database)
    plan = load_plan(args.plan)
    if args.confirm != plan["run_id"] or not args.admin:
        raise ValueError("admin and exact plan run ID required")
    password = getpass.getpass("管理员当前口令：")
    engine = engine_for(database)
    try:
        with Session(engine) as session:
            admin = session.scalar(select(User).where(User.username == args.admin))
            if admin is None or not verify_user_password(admin, password):
                raise ValueError("administrator password verification failed")
            result = atomic_confirm(session, plan=plan, administrator=admin)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
