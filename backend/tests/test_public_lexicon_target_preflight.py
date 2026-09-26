"""Read-only target checks use only synthetic plans and temporary databases."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.models import LexiconEntry, User
from tests.test_public_lexicon_confirm import (
    _build_plan,
    _counts,
    _standard_decisions,
    _system_lexicon,
)


@pytest.fixture()
def admin(make_world):
    world = make_world("target-preflight-admin", role="admin")
    yield world
    world.client.__exit__(None, None, None)


def _database(admin) -> Path:
    with admin.session() as session:
        return Path(session.bind.url.database)


def _database_files(database: Path) -> dict[str, str]:
    """Catch new or changed SQLite sidecars as well as changes to the main file."""
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in database.parent.iterdir() if path.is_file()
    }


def _plan(tmp_path: Path, token: str, target: str):
    root = tmp_path / "sources"
    root.mkdir()
    return root, _build_plan(
        root, token=token, target=target, decisions=_standard_decisions(token)
    )


def test_preflight_reports_target_match_content_difference_and_never_writes(
    admin, tmp_path: Path
) -> None:
    from app.services.public_lexicon_target_preflight import preflight_target

    root, plan = _plan(tmp_path, "preflight", "synthetic-public")
    database = _database(admin)
    with admin.session() as session:
        target = _system_lexicon(session, "synthetic-public", "synthetic-preflight")
        session.add(LexiconEntry(
            lexicon_id=target.id, word="WORDpreflight", normalized_word="wordpreflight",
            source_meanings=["库内旧释义"], source_raw="existing raw", sequence=9,
        ))
        session.commit()
        before_counts = _counts(session)
    before_bytes = hashlib.sha256(database.read_bytes()).hexdigest()
    before_files = _database_files(database)

    report = preflight_target(database, plan=plan, source_root=root)

    assert report["target"] == {"exists": True, "id": target.id, "name": "synthetic-public"}
    assert report["counts"] == {"new": 0, "matched": 1, "blocked": 0, "excluded": 1}
    match = report["entries"][0]
    assert match["normalized_word"] == "wordpreflight"
    assert match["status"] == "matched"
    assert match["content_differences"]["source_meanings"] == {
        "existing": ["库内旧释义"], "planned": ["主词表释义-preflight"]
    }
    assert match["content_differences"]["sequence"] == {"existing": 9, "planned": 1}
    assert report["sources"][0]["file_sha256"] == plan["sources"][0]["file"]["sha256"]
    assert report["sources"][0]["artifact_status"] == "new"
    assert report["technical_preflight_passed"] is True
    assert report["authorization_review"]["status"] == "not_assessed"
    assert "ready_for_confirmation" not in report
    with admin.session() as session:
        assert _counts(session) == before_counts
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before_bytes
    assert _database_files(database) == before_files


def test_preflight_blocks_missing_target_and_changed_source_without_writing(
    admin, tmp_path: Path
) -> None:
    from app.services.public_lexicon_target_preflight import preflight_target

    root, plan = _plan(tmp_path, "missing", "absent-public")
    database = _database(admin)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    before_files = _database_files(database)
    report = preflight_target(database, plan=plan, source_root=root)
    assert report["target"] == {"exists": False, "id": None, "name": "absent-public"}
    assert report["counts"] == {"new": 0, "matched": 0, "blocked": 1, "excluded": 1}
    assert report["technical_preflight_passed"] is False

    (root / "primary.csv").write_text("head,cn\nChanged,改变\n", encoding="utf-8")
    changed = preflight_target(database, plan=plan, source_root=root)
    assert any("文件内容已变化" in blocker for blocker in changed["blockers"])
    assert changed["counts"]["blocked"] == 1
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert _database_files(database) == before_files


def test_preflight_reports_reused_artifact_provenance_mismatch(admin, tmp_path: Path) -> None:
    from app.services.public_lexicon_confirm import confirm_plan
    from app.services.public_lexicon_plan import plan_digest
    from app.services.public_lexicon_target_preflight import preflight_target

    root, plan = _plan(tmp_path, "archive", "archive-public")
    database = _database(admin)
    with admin.session() as session:
        _system_lexicon(session, "archive-public", "synthetic-archive")
        confirm_plan(
            session, plan=plan, administrator=session.get(User, admin.user_id),
            source_root=root,
        )
        before_counts = _counts(session)
    plan["sources"][0]["provenance"]["license_id"] = "changed-declaration"
    plan["plan_sha256"] = plan_digest(plan)
    before_bytes = hashlib.sha256(database.read_bytes()).hexdigest()

    report = preflight_target(database, plan=plan, source_root=root)

    source = report["sources"][0]
    assert source["artifact_status"] == "provenance_mismatch"
    assert report["sources"][1]["artifact_status"] == "reused"
    assert source["provenance_differences"]["license_id"] == {
        "existing": "synthetic-test-only", "planned": "changed-declaration"
    }
    assert "source_artifact_provenance_mismatch:primary" in report["blockers"]
    assert report["technical_preflight_passed"] is False
    with admin.session() as session:
        assert _counts(session) == before_counts
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before_bytes


def test_preflight_cli_uses_explicit_read_only_database(admin, tmp_path: Path, capsys) -> None:
    from app.cli import main

    root, plan = _plan(tmp_path, "cli", "cli-public")
    database = _database(admin)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    exit_code = main([
        "public-lexicon", "preflight-target", "--plan", str(plan_file),
        "--source-root", str(root), "--database", str(database),
    ])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert report["target"]["exists"] is False
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_preflight_recognizes_exact_plan_already_applied(admin, tmp_path: Path) -> None:
    from app.services.public_lexicon_confirm import confirm_plan
    from app.services.public_lexicon_target_preflight import preflight_target

    root, plan = _plan(tmp_path, "repeated", "repeated-public")
    database = _database(admin)
    with admin.session() as session:
        _system_lexicon(session, "repeated-public", "synthetic-repeated")
        first = confirm_plan(
            session, plan=plan, administrator=session.get(User, admin.user_id),
            source_root=root,
        )
    report = preflight_target(database, plan=plan, source_root=root)
    assert report["prior_run"] == {"id": first["import_run_id"], "run_id": first["run_id"]}
    assert report["counts"] == {"new": 0, "matched": 0, "blocked": 0, "excluded": 1}
    assert report["technical_preflight_passed"] is True


def test_preflight_reports_unready_plan_as_blocked(admin, tmp_path: Path) -> None:
    from app.services.public_lexicon_plan import plan_digest
    from app.services.public_lexicon_target_preflight import preflight_target

    root, plan = _plan(tmp_path, "unready", "unready-public")
    database = _database(admin)
    with admin.session() as session:
        _system_lexicon(session, "unready-public", "synthetic-unready")
    plan["confirmation_ready"] = False
    plan["confirmation_blockers"] = ["manual_adjudication_required"]
    plan["plan_sha256"] = plan_digest(plan)

    report = preflight_target(database, plan=plan, source_root=root)

    assert "manual_adjudication_required" in report["blockers"]
    assert report["counts"] == {"new": 0, "matched": 0, "blocked": 1, "excluded": 1}


def test_draft_authorization_is_prominently_pending_and_blocks_cli(
    admin, tmp_path: Path, capsys
) -> None:
    from app.cli import main
    from app.services.public_lexicon_plan import plan_digest

    root, plan = _plan(tmp_path, "draft", "draft-public")
    database = _database(admin)
    with admin.session() as session:
        _system_lexicon(session, "draft-public", "synthetic-draft")
    plan["sources"][0]["provenance"]["use_scope"] = "预演草案·未获批准：仅本机只读预演"
    plan["sources"][0]["provenance"]["display_scope"] = "预演草案·未获批准：不可向用户展示"
    plan["plan_sha256"] = plan_digest(plan)
    plan_file = tmp_path / "draft-plan.json"
    plan_file.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    before_files = _database_files(database)

    exit_code = main([
        "public-lexicon", "preflight-target", "--plan", str(plan_file),
        "--source-root", str(root), "--database", str(database),
    ])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert report["authorization_review"]["status"] == "pending_owner_approval"
    assert report["authorization_review"]["pending_sources"] == ["primary"]
    assert "仍待负责人批准" in report["authorization_review"]["message"]
    assert report["sources"][0]["declared_approval_state"] == "explicitly_unapproved"
    assert "owner_approval_pending:primary" in report["blockers"]
    assert report["technical_preflight_passed"] is False
    assert report["counts"] == {"new": 0, "matched": 0, "blocked": 1, "excluded": 1}
    assert "ready_for_confirmation" not in report
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert _database_files(database) == before_files


def test_draft_warning_survives_missing_target_schema(admin, tmp_path: Path) -> None:
    from app.services.public_lexicon_plan import plan_digest
    from app.services.public_lexicon_target_preflight import preflight_target

    root, plan = _plan(tmp_path, "schema", "schema-public")
    plan["sources"][0]["provenance"]["display_scope"] = "预演草案·未获批准"
    plan["plan_sha256"] = plan_digest(plan)
    empty_database = tmp_path / "empty.db"
    with sqlite3.connect(empty_database):
        pass

    report = preflight_target(empty_database, plan=plan, source_root=root)

    assert report["authorization_review"]["status"] == "pending_owner_approval"
    assert "owner_approval_pending:primary" in report["blockers"]
    assert "target_schema_missing: expected Phase 2.9 import tables" in report["blockers"]
