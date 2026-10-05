"""Authenticated release CLI for the one locked NETEM gap candidate.

The existing public import remains insert-only. Writes here require the exact
current release commit, run ID, candidate authority, and an active admin password.
"""

from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path

from netem_release import (
    ROOT,
    check_code,
    engine_for,
    guard_database,
    readonly,
    verify_revision,
    write_json,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User
from app.services.auth import verify_user_password
from app.services.public_lexicon_gap import build_gap_plan, confirm_gap


def authority():
    index = json.loads((ROOT / "docs/NETEM-GAP-CANDIDATE-2026-10-05.json").read_text("utf8"))
    return ROOT / index["candidate_directory"], index["candidate_sha256"]


def authenticated_execute(*, database: Path, plan: dict, code_sha: str, username: str,
                          password: str, production: bool = False, action: str = "apply") -> dict:
    check_code(code_sha)
    database = guard_database(database, production=production)
    verify_revision(database)
    # Authenticate with a read-only connection before acquiring the write lock.
    with readonly(database) as con:
        row = con.execute("select id,username,role,is_active,password_hash from user where username=?", (username,)).fetchone()
    if not row or not row[3] or row[2] != "admin":
        raise ValueError("active local administrator required")
    admin = User(id=row[0], username=row[1], role=row[2], is_active=bool(row[3]), password_hash=row[4])
    if not verify_user_password(admin, password):
        raise ValueError("administrator password verification failed; no writes")
    candidate, expected = authority()
    engine = engine_for(database)
    try:
        with Session(engine) as session:
            current = session.scalar(select(User).where(User.id == admin.id))
            if current is None or current.password_hash != row[4]:
                raise ValueError("administrator changed since authentication")
            return confirm_gap(session, plan=plan, administrator=current, candidate=candidate,
                               expected_sha=expected, action=action)
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["plan", "apply", "retract"])
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--code-sha", required=True)
    parser.add_argument("--admin")
    parser.add_argument("--confirm")
    parser.add_argument("--production-update", action="store_true")
    args = parser.parse_args()
    check_code(args.code_sha)
    if args.action == "plan":
        if args.plan.exists():
            raise ValueError("refusing to overwrite locked plan")
        verify_revision(args.database)
        candidate, expected = authority()
        write_json(args.plan, build_gap_plan(args.database, candidate=candidate, expected_sha=expected))
        return
    plan = json.loads(args.plan.read_text("utf8"))
    if args.confirm != plan["run_id"] or not args.admin:
        raise ValueError("admin and exact run ID required")
    password = getpass.getpass("管理员当前口令（仅本机）：")
    result = authenticated_execute(database=args.database, plan=plan, code_sha=args.code_sha,
        username=args.admin, password=password, production=args.production_update, action=args.action)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
