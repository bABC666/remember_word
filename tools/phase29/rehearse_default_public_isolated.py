"""Create a fresh, isolated migrated NETEM v2 browser acceptance database.

Refuses any target outside test-artifacts and any existing database. This is a
local rehearsal, not a production import command.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAFE_ROOT = (ROOT / "test-artifacts").resolve()


def run(output: Path, package: Path) -> dict:
    target = output.resolve()
    if not target.is_relative_to(SAFE_ROOT) or target == SAFE_ROOT:
        raise ValueError("isolated output must be a child of test-artifacts")
    database = target / "vocab.db"
    if database.exists():
        raise ValueError(f"refusing to reuse an existing database: {database}")
    password = os.environ.get("NETEM_ISOLATED_PASSWORD", "")
    if len(password) < 12:
        raise ValueError("set NETEM_ISOLATED_PASSWORD for the isolated browser account")
    target.mkdir(parents=True, exist_ok=True)
    os.environ["VOCAB_DATA_DIR"] = str(target)
    os.environ["VOCAB_DATABASE_PATH"] = str(database)
    os.environ["VOCAB_ENABLE_OCR"] = "false"

    from alembic.config import Config

    from alembic import command

    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    command.upgrade(config, "head")

    from app.api.deps import ensure_user_settings
    from app.db import get_session_factory, verify_schema_revision
    from app.models import Lexicon, User
    from app.security import hash_password
    from app.services.public_lexicon_confirm import confirm_plan
    from app.services.public_lexicon_plan import build_plan

    verify_schema_revision(database)
    plan = build_plan(manifest_path=Path("manifest.json"), source_root=package.resolve(),
                      decisions_path=Path("decisions.json"), target_lexicon="NETEM isolated v2")
    if not plan["confirmation_ready"] or plan["summary"]["ready_entries"] != 5528:
        raise ValueError(f"public plan not ready: {plan['confirmation_blockers']}")
    with get_session_factory()() as session:
        admin = User(username="netem_isolated_admin", display_name="Isolated admin",
                     role="admin", password_hash=hash_password(password))
        learner = User(username="netem_isolated", display_name="Isolated learner",
                       role="user", password_hash=hash_password(password))
        session.add_all([admin, learner])
        session.flush()
        ensure_user_settings(session, admin)
        ensure_user_settings(session, learner)
        session.add(Lexicon(name="NETEM isolated v2", description="Local evaluation only; no publication licence approved",
                            visibility="public", source_type="netem"))
        session.commit()
        result = confirm_plan(session, plan=plan, administrator=admin, source_root=package.resolve())
        if result["entries_created"] != 5528 or result["entries_matched"]:
            raise ValueError(f"unexpected import result: {result}")
        lexicon_id = result["target_lexicon"]["id"]
    return {"database": str(database), "revision": "0015_session_autoincrement",
            "lexicon_id": lexicon_id, "entries_created": result["entries_created"],
            "evidence_written": result["evidence_written"],
            "plan_sha256": plan["plan_sha256"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--package", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.package), ensure_ascii=False, indent=2))
