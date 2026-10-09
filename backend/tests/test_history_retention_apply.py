"""The G6 retention apply flow: a locked plan, a backup, and fail-closed deletion.

The read-only preview and the archive/credential verifier already existed. What these
tests pin is the destructive half: a plan that fixes the candidate IDs, the UTC cutoff
and every row hash; a pre-cleanup backup taken with the SQLite backup API; an archive
plus a *pending* credential written before anything is deleted; deletion inside one
``BEGIN IMMEDIATE`` transaction, by explicit ID only, with the deleted count checked;
strict verification after the commit; and only then the atomic publication of the
``committed`` credential.

Every failure path must fail closed: the database is left as it was, no committed
credential appears, and the public verifier refuses to explain any loss. A committed
run must stay verifiable afterwards, including from the pre-cleanup backup itself --
that backup is what an operator restores from, so it has to keep passing the ordinary
strict check.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app import cli, history_retention

TOOLS = Path(__file__).resolve().parents[2] / "tools"

#: Fixed "now" so the cutoff and the run ID are reproducible.
NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
RUN_ID = "run-001"

#: Rows of the fixture. Only 1, 2 and 5 are candidates: 3 is not allowlisted, 4 is
#: inside the retention window, 6 is the highest ID and always retained.
ROWS = [
    (1, 7, "login_failed", "2024-01-01 00:00:00", "user", 7, '{"username":"secret"}'),
    (2, 7, "user_login", "2024-06-01 00:00:00", "user", 7, '{"token":"secret"}'),
    (3, 7, "user_updated", "2024-01-01 00:00:00", "user", 7, '{"role":"admin"}'),
    (4, 7, "login_failed", "2026-09-01 00:00:00", "user", 7, "{}"),
    (5, None, "article_word_lookup", "2023-05-05 00:00:00", "", None, "{}"),
    (6, 7, "user_login", "2026-09-23 11:59:59", "user", 7, "{}"),
]
CANDIDATES = [1, 2, 5]


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verified_db = load_tool("verified_db")


class World:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.database = root / "app-data" / "retention.db"
        self.baseline_path = root / "baseline.json"
        self.evidence = root / "history-retention"
        self.drafts = root / "history-retention-drafts"

    def rows(self) -> dict[int, tuple]:
        with sqlite3.connect(self.database) as connection:
            return {
                row[0]: row
                for row in connection.execute(
                    "select id, user_id, event_type, timestamp, entity_type, entity_id, payload "
                    "from history_event"
                )
            }

    def plan(self, **kwargs) -> dict:
        options = {"now": NOW, "run_id": RUN_ID}
        options.update(kwargs)
        return history_retention.build_plan(
            self.database, baseline_path=self.baseline_path, **options
        )

    def apply(self, plan: dict, **kwargs) -> dict:
        options = {
            "baseline_path": self.baseline_path,
            "evidence_dir": self.evidence,
            "drafts_dir": self.drafts,
        }
        options.update(kwargs)
        return history_retention.apply_plan(self.database, plan, **options)

    def verified(self) -> tuple[bool, dict]:
        return verified_db.compare_against_baseline(
            self.database,
            verified_db.load_baseline(self.baseline_path),
            retention_dir=self.evidence,
        )


@pytest.fixture()
def world(tmp_path: Path) -> World:
    world = World(tmp_path)
    world.database.parent.mkdir()
    with sqlite3.connect(world.database) as connection:
        connection.executescript(
            """
            create table alembic_version (version_num text not null);
            insert into alembic_version values ('0007_bridge_foreign_keys');
            create table word (id integer primary key, word text not null);
            insert into word values (1, 'signal');
            create table history_event (
                id integer primary key, user_id integer, event_type text not null,
                timestamp text not null, entity_type text not null,
                entity_id integer, payload text not null
            );
            """
        )
        connection.executemany("insert into history_event values (?,?,?,?,?,?,?)", ROWS)
    verified_db.save_baseline(verified_db.capture_baseline(world.database), world.baseline_path)
    return world


def execute(database: Path, sql: str, parameters: tuple = ()) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(sql, parameters)


def evidence_files(world: World) -> set[str]:
    if not world.evidence.exists():
        return set()
    return {path.name for path in world.evidence.iterdir()}


# --- the locked plan ---------------------------------------------------------


def test_the_plan_locks_the_candidate_ids_the_cutoff_and_every_row_hash(world: World) -> None:
    plan = world.plan()

    assert plan["run_id"] == RUN_ID
    assert plan["cutoff_utc"] == "2025-09-23T12:00:00Z"
    assert plan["retention_days"] == 365
    assert plan["event_types"] == [
        "login_failed",
        "reauth_failed",
        "user_login",
        "article_word_lookup",
    ]
    assert plan["candidate_ids"] == CANDIDATES
    assert plan["candidate_count"] == 3
    assert plan["max_history_event_id"] == 6
    assert plan["database"]["revision"] == "0007_bridge_foreign_keys"
    # Every candidate carries a full-row hash and an identity hash, so a later
    # re-read can be compared row by row rather than by count.
    assert set(plan["candidates"]) == {"1", "2", "5"}
    for entry in plan["candidates"].values():
        assert len(entry["full"]) == 64 and len(entry["identity"]) == 64
    # Rows that will NOT be deleted are hashed too: a run must not proceed after
    # something it is not allowed to remove has disappeared or changed.
    assert set(plan["scan_hashes"]) == {"1", "2", "3", "4", "5", "6"}
    assert plan["columns"] == [
        "id",
        "user_id",
        "event_type",
        "timestamp",
        "entity_type",
        "entity_id",
        "payload",
    ]
    # A plan is reviewable evidence, not a copy of the audit trail.
    assert "secret" not in json.dumps(plan)
    assert plan["policy_status"] == "confirmed"
    assert plan["production_run_approved"] is False
    assert plan["plan_sha256"] == history_retention.plan_digest(plan)


def test_the_plan_digest_covers_the_candidate_set_and_the_cutoff(world: World) -> None:
    plan = world.plan()
    for field, value in (("cutoff_utc", "2025-09-23T13:00:00Z"), ("candidate_ids", [1, 2])):
        tampered = {**plan, field: value}
        assert history_retention.plan_digest(tampered) != plan["plan_sha256"]


def test_the_plan_file_is_written_once_and_never_overwritten(world: World) -> None:
    path = world.root / "plan.json"
    history_retention.write_plan(world.plan(), path)
    saved = path.read_bytes()

    with pytest.raises(FileExistsError):
        history_retention.write_plan(world.plan(), path)
    assert path.read_bytes() == saved


def test_an_empty_candidate_set_is_refused(world: World) -> None:
    execute(world.database, "delete from history_event where id in (1, 2, 5)")
    verified_db.save_baseline(
        verified_db.capture_baseline(world.database), world.baseline_path
    )

    with pytest.raises(history_retention.RetentionError):
        world.plan()


# --- drift after the preview -------------------------------------------------


def test_a_newly_appearing_candidate_refuses_the_run(world: World) -> None:
    plan = world.plan()
    # An old-dated allowlisted row inserted after the preview is a candidate the
    # operator never reviewed, so the run must not proceed.
    execute(
        world.database,
        "insert into history_event values (7, 7, 'login_failed', '2024-02-02 00:00:00', "
        "'user', 7, '{}')",
    )

    with pytest.raises(history_retention.RetentionError) as refusal:
        world.apply(plan)

    assert "drift" in str(refusal.value).lower() or "候选" in str(refusal.value)
    assert set(world.rows()) == {1, 2, 3, 4, 5, 6, 7}
    assert evidence_files(world) == set()


def test_a_changed_candidate_refuses_the_run(world: World) -> None:
    plan = world.plan()
    execute(world.database, "update history_event set payload = '{\"x\":1}' where id = 2")

    with pytest.raises(history_retention.RetentionError):
        world.apply(plan)

    assert world.rows()[2][-1] == '{"x":1}'.replace('\\"', '"') or world.rows()[2][-1] != "{}"
    assert set(world.rows()) == {1, 2, 3, 4, 5, 6}
    assert evidence_files(world) == set()


def test_a_deleted_candidate_refuses_the_run(world: World) -> None:
    plan = world.plan()
    execute(world.database, "delete from history_event where id = 5")

    with pytest.raises(history_retention.RetentionError):
        world.apply(plan)

    assert set(world.rows()) == {1, 2, 3, 4, 6}
    assert evidence_files(world) == set()


def test_losing_a_row_that_the_plan_does_not_delete_refuses_the_run(world: World) -> None:
    """A non-candidate disappearing is unexplained loss, not a cleanup."""
    plan = world.plan()
    execute(world.database, "delete from history_event where id = 3")

    with pytest.raises(history_retention.RetentionError):
        world.apply(plan)

    assert set(world.rows()) == {1, 2, 4, 5, 6}
    assert evidence_files(world) == set()


def test_a_plan_made_against_another_database_is_refused(world: World, tmp_path: Path) -> None:
    plan = world.plan()
    other = tmp_path / "other" / "app-data" / "retention.db"
    other.parent.mkdir(parents=True)
    other.write_bytes(world.database.read_bytes())

    with pytest.raises(history_retention.RetentionError):
        history_retention.apply_plan(
            other,
            plan,
            baseline_path=world.baseline_path,
            evidence_dir=world.evidence,
            drafts_dir=world.drafts,
        )


def test_a_plan_whose_digest_was_changed_is_refused(world: World) -> None:
    plan = world.plan()
    plan["candidate_ids"] = [1]

    with pytest.raises(history_retention.RetentionError):
        world.apply(plan)


# --- backup and archive failures ---------------------------------------------


def test_a_refused_backup_path_stops_the_run_before_any_delete(world: World) -> None:
    plan = world.plan()
    world.drafts.mkdir(parents=True)
    (world.drafts / f"{RUN_ID}.before.db").mkdir()

    with pytest.raises(history_retention.RetentionError):
        world.apply(plan)

    assert set(world.rows()) == {1, 2, 3, 4, 5, 6}
    assert evidence_files(world) == set()


def test_a_refused_archive_path_stops_the_run_before_any_delete(world: World) -> None:
    plan = world.plan()
    world.drafts.mkdir(parents=True)
    (world.drafts / f"{RUN_ID}.archive.jsonl").mkdir()

    with pytest.raises(history_retention.RetentionError):
        world.apply(plan)

    assert set(world.rows()) == {1, 2, 3, 4, 5, 6}
    assert evidence_files(world) == set()


def test_a_backup_failure_keeps_the_database_and_publishes_nothing(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = world.plan()

    def explode(*_args, **_kwargs):
        raise history_retention.RetentionError("backup device failed")

    monkeypatch.setattr(history_retention, "_create_pre_backup", explode)

    with pytest.raises(history_retention.RetentionError):
        world.apply(plan)

    assert set(world.rows()) == {1, 2, 3, 4, 5, 6}
    assert evidence_files(world) == set()


def test_an_archive_failure_keeps_the_database_and_publishes_nothing(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = world.plan()

    def explode(*_args, **_kwargs):
        raise history_retention.RetentionError("archive device failed")

    monkeypatch.setattr(history_retention, "_write_archive", explode)

    with pytest.raises(history_retention.RetentionError):
        world.apply(plan)

    assert set(world.rows()) == {1, 2, 3, 4, 5, 6}
    assert evidence_files(world) == set()


def test_a_backup_that_lost_a_baseline_row_cannot_authorize_the_run(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-cleanup backup has to preserve the baseline too, not just the rows."""
    plan = world.plan()
    original = history_retention._create_pre_backup

    def sabotage(database: Path, target: Path) -> Path:
        created = original(database, target)
        with sqlite3.connect(created) as connection:
            connection.execute("delete from word where id = 1")
        return created

    monkeypatch.setattr(history_retention, "_create_pre_backup", sabotage)

    with pytest.raises(history_retention.RetentionError):
        world.apply(plan)

    assert set(world.rows()) == {1, 2, 3, 4, 5, 6}
    assert evidence_files(world) == set()


# --- the transaction ---------------------------------------------------------


def test_a_failure_inside_the_transaction_rolls_every_row_back(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = world.plan()
    original_delete = history_retention._delete_rows

    def delete_then_fail(connection: sqlite3.Connection, ids: list[int]) -> int:
        original_delete(connection, ids)
        raise history_retention.RetentionError("injected failure after the delete")

    monkeypatch.setattr(history_retention, "_delete_rows", delete_then_fail)

    with pytest.raises(history_retention.RetentionError):
        world.apply(plan)

    # Rolled back: every row is still there, and nothing was published.
    assert set(world.rows()) == {1, 2, 3, 4, 5, 6}
    assert evidence_files(world) == set()
    verified, report = world.verified()
    assert verified, report["failures"]


def test_a_mismatched_delete_count_rolls_back(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = world.plan()
    original_delete = history_retention._delete_rows

    def delete_too_few(connection: sqlite3.Connection, ids: list[int]) -> int:
        original_delete(connection, ids[:-1])
        return len(ids[:-1])

    monkeypatch.setattr(history_retention, "_delete_rows", delete_too_few)

    with pytest.raises(history_retention.RetentionError):
        world.apply(plan)

    assert set(world.rows()) == {1, 2, 3, 4, 5, 6}
    assert evidence_files(world) == set()


# --- the committed run -------------------------------------------------------


def test_a_committed_run_deletes_only_the_planned_ids_and_stays_verifiable(world: World) -> None:
    plan = world.plan()
    result = world.apply(plan)

    assert result["run_id"] == RUN_ID
    assert result["deleted"] == 3
    assert result["committed_manifest"].endswith(f"{RUN_ID}.manifest.json")

    remaining = world.rows()
    assert set(remaining) == {3, 4, 6}
    assert remaining[3][2] == "user_updated"  # not allowlisted
    assert remaining[6][3] == "2026-09-23 11:59:59"  # highest ID, always retained

    # The evidence directory holds exactly one committed run: the three files the
    # verifier expects and nothing else.
    assert evidence_files(world) == {
        f"{RUN_ID}.manifest.json",
        f"{RUN_ID}.archive.jsonl",
        f"{RUN_ID}.before.db",
    }

    verified, report = world.verified()
    assert verified, report["failures"]
    assert report["history_event_archived"]["ids"] == ["1", "2", "5"]

    # The pre-cleanup backup is what an operator restores from, so it must keep
    # verifying on its own -- including after the pruning.
    backup_verified, backup_report = verified_db.compare_against_baseline(
        world.evidence / f"{RUN_ID}.before.db",
        verified_db.load_baseline(world.baseline_path),
    )
    assert backup_verified, backup_report["failures"]


def test_the_archive_holds_the_exact_rows_that_were_removed(world: World) -> None:
    plan = world.plan()
    before = world.rows()
    world.apply(plan)

    manifest = json.loads((world.evidence / f"{RUN_ID}.manifest.json").read_text("utf-8"))
    lines = (
        (world.evidence / f"{RUN_ID}.archive.jsonl").read_text("utf-8").splitlines()
    )

    assert manifest["state"] == "committed"
    assert manifest["row_ids"] == CANDIDATES
    assert manifest["plan_sha256"] == plan["plan_sha256"]
    assert manifest["cutoff_utc"] == plan["cutoff_utc"]
    assert manifest["retention_days"] == 365
    assert json.loads(lines[0])["columns"] == plan["columns"]
    for index, row_id in enumerate(CANDIDATES, start=1):
        values = json.loads(lines[index])["values"]
        assert values == list(before[row_id])
        assert verified_db.row_hash(tuple(values)) == manifest["full_hashes"][str(row_id)]

    assert hashlib.sha256((world.evidence / f"{RUN_ID}.archive.jsonl").read_bytes()).hexdigest() == (
        manifest["archive_sha256"]
    )


def test_the_pending_credential_never_explains_a_loss_on_its_own(world: World) -> None:
    """Only the published ``committed`` manifest is evidence for the verifier."""
    plan = world.plan()
    world.apply(plan)
    manifest_path = world.evidence / f"{RUN_ID}.manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["state"] = "pending"
    manifest_path.write_text(json.dumps(manifest), "utf-8")

    verified, report = world.verified()

    assert not verified
    assert any("archive evidence invalid" in failure for failure in report["failures"])


def test_a_committed_run_can_be_followed_by_a_second_run(world: World) -> None:
    first = world.plan()
    world.apply(first)
    # An old event that is not the newest one, so the highest-ID rule does not protect
    # it: this is a candidate the second run may archive.
    execute(
        world.database,
        "insert into history_event values (7, 7, 'reauth_failed', '2024-03-03 00:00:00', "
        "'user', 7, '{}')",
    )
    execute(
        world.database,
        "insert into history_event values (8, 7, 'user_login', '2026-09-23 11:00:00', "
        "'user', 7, '{}')",
    )
    second = world.plan(run_id="run-002")

    assert second["candidate_ids"] == [7]
    world.apply(second)

    verified, report = world.verified()
    assert verified, report["failures"]
    assert report["history_event_archived"]["ids"] == ["1", "2", "5", "7"]
    assert evidence_files(world) == {
        name
        for run in ("run-001", "run-002")
        for name in (
            f"{run}.manifest.json",
            f"{run}.archive.jsonl",
            f"{run}.before.db",
        )
    }


# --- the published credential ------------------------------------------------


def test_a_crash_before_publishing_the_credential_fails_closed(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Committed but unpublished: the loss may not be explained by anything yet."""
    plan = world.plan()

    def explode(*_args, **_kwargs):
        raise history_retention.RetentionError("publish failed")

    monkeypatch.setattr(history_retention, "_publish_credential", explode)

    with pytest.raises(history_retention.RetentionError) as failure:
        world.apply(plan)

    message = str(failure.value)
    assert f"{RUN_ID}.before.db" in message  # tells the operator what to restore
    assert set(world.rows()) == {3, 4, 6}  # the delete did happen

    # Nothing was published, and the run's own evidence is still in the draft, which
    # is what the operator restores from.
    assert evidence_files(world) == set()
    assert (world.drafts / f"{RUN_ID}.before.db").is_file()

    verified, report = world.verified()
    assert not verified
    assert any("history_event" in item for item in report["failures"])

    # The pre-cleanup backup still holds every row, so the restore path exists -- the
    # draft directory is where an unpublished run keeps its evidence.
    restored = world.root / "restored.db"
    restored.write_bytes((world.drafts / f"{RUN_ID}.before.db").read_bytes())
    restored_verified, restored_report = verified_db.compare_against_baseline(
        restored, verified_db.load_baseline(world.baseline_path)
    )
    assert restored_verified, restored_report["failures"]


def test_an_archived_id_reused_with_other_content_is_caught(world: World) -> None:
    plan = world.plan()
    world.apply(plan)
    execute(
        world.database,
        "insert into history_event values (1, 9, 'user_login', '2024-01-01 00:00:00', "
        "'user', 9, '{}')",
    )

    verified, report = world.verified()

    assert not verified
    assert any("conflict" in failure or "history_event" in failure for failure in report["failures"])


def test_a_lost_row_that_the_archive_cannot_explain_still_fails(world: World) -> None:
    plan = world.plan()
    world.apply(plan)
    execute(world.database, "delete from history_event where id = 4")

    verified, report = world.verified()

    assert not verified
    assert report["failures"]


# --- a live database is in WAL mode ------------------------------------------


@pytest.fixture()
def wal_world(tmp_path: Path) -> World:
    """A world whose database is in WAL mode, like the running application's.

    The staging drill caught this: a copy taken from a WAL database inherits WAL mode,
    and the ``-wal``/``-shm`` sidecars then land in the evidence directory, where the
    verifier expects exactly three files per run.
    """
    world = World(tmp_path)
    world.database.parent.mkdir()
    with sqlite3.connect(world.database) as connection:
        connection.executescript(
            """
            pragma journal_mode=wal;
            create table alembic_version (version_num text not null);
            insert into alembic_version values ('0007_bridge_foreign_keys');
            create table word (id integer primary key, word text not null);
            insert into word values (1, 'signal');
            create table history_event (
                id integer primary key, user_id integer, event_type text not null,
                timestamp text not null, entity_type text not null,
                entity_id integer, payload text not null
            );
            """
        )
        connection.executemany("insert into history_event values (?,?,?,?,?,?,?)", ROWS)
    # Leave committed content in the WAL: a plain file copy would miss it.
    with sqlite3.connect(world.database) as connection:
        assert connection.execute("pragma journal_mode").fetchone()[0] == "wal"
        connection.execute(
            "insert into history_event values (7, 7, 'user_login', '2026-09-23 11:00:00', "
            "'user', 7, '{}')"
        )
        connection.commit()
    verified_db.save_baseline(verified_db.capture_baseline(world.database), world.baseline_path)
    return world


def test_a_wal_database_prunes_and_leaves_one_self_contained_backup(wal_world: World) -> None:
    plan = wal_world.plan()
    result = wal_world.apply(plan)

    assert result["deleted"] == 3
    assert set(wal_world.rows()) == {3, 4, 6, 7}
    # Exactly the three files: the pre-cleanup backup is one self-contained database,
    # not a main file that needs sidecars to be complete.
    assert evidence_files(wal_world) == {
        f"{RUN_ID}.manifest.json",
        f"{RUN_ID}.archive.jsonl",
        f"{RUN_ID}.before.db",
    }
    verified, report = wal_world.verified()
    assert verified, report["failures"]

    backup = wal_world.evidence / f"{RUN_ID}.before.db"
    assert not backup.with_name(backup.name + "-wal").exists()
    with sqlite3.connect(backup) as connection:
        assert connection.execute("pragma journal_mode").fetchone()[0] != "wal"
        assert connection.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "select count(*) from history_event"
        ).fetchone()[0] == 7  # every row, including the ones that were pruned


def _cli_env(monkeypatch: pytest.MonkeyPatch, world: World) -> None:
    monkeypatch.setenv("VOCAB_DATABASE_PATH", str(world.database))
    monkeypatch.setattr(cli, "verify_schema_revision", lambda path: None)
    monkeypatch.setattr(
        cli, "get_settings", lambda: pytest.fail("retention CLI called writing settings")
    )


def test_the_cli_plans_then_applies_only_with_the_exact_run_id(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _cli_env(monkeypatch, world)
    plan_path = world.root / "plan.json"

    assert cli.main([
        "history-retention", "preview", "--plan", str(plan_path),
        "--baseline", str(world.baseline_path),
    ]) == 0
    printed = capsys.readouterr().out
    assert (world.root / "plan.json").is_file()
    plan = json.loads(plan_path.read_text("utf-8"))
    assert plan["run_id"] in printed
    assert plan["cutoff_utc"] in printed
    assert "secret" not in printed
    assert "生产执行未批准" in printed

    base = [
        "history-retention",
        "apply",
        "--plan",
        str(plan_path),
        "--evidence-dir",
        str(world.evidence),
        "--baseline",
        str(world.baseline_path),
        "--drafts-dir",
        str(world.drafts),
    ]
    # No confirmation at all, then a wrong one: both refuse and change nothing.
    assert cli.main(base) == 1
    assert "confirm" in capsys.readouterr().out.lower()
    assert cli.main([*base, "--confirm", "run-999"]) == 1
    capsys.readouterr()
    assert set(world.rows()) == {1, 2, 3, 4, 5, 6}
    assert evidence_files(world) == set()

    assert cli.main([*base, "--confirm", plan["run_id"]]) == 0
    output = capsys.readouterr().out
    assert "已发布" in output
    assert set(world.rows()) == {3, 4, 6}


def test_the_cli_refuses_the_real_data_directory(
    monkeypatch: pytest.MonkeyPatch, real_data_dir: Path
) -> None:
    """The operator path must not reach production by accident."""
    from app.testing_guards import UnsafeDatabasePathError

    monkeypatch.setenv("VOCAB_DATABASE_PATH", str(real_data_dir / "vocab.db"))
    monkeypatch.setattr(cli, "verify_schema_revision", lambda path: None)

    with pytest.raises(UnsafeDatabasePathError):
        cli.main(["history-retention", "preview"])


def test_apply_refuses_a_protected_database_without_the_explicit_flag(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = world.plan()
    monkeypatch.setattr(history_retention, "is_protected_database", lambda path: True)

    with pytest.raises(history_retention.RetentionError) as refusal:
        world.apply(plan)
    assert "allow-production" in str(refusal.value)
    assert set(world.rows()) == {1, 2, 3, 4, 5, 6}

    # With the flag the run is allowed to proceed (this is the production switch the
    # operator has to name explicitly; nothing in this batch used it on real data).
    result = world.apply(plan, allow_production=True)
    assert result["deleted"] == 3


def test_apply_refuses_while_the_retention_window_is_disabled(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = world.plan()
    monkeypatch.setenv(history_retention.RETENTION_DAYS_ENV, "0")

    with pytest.raises(history_retention.RetentionError):
        world.apply(plan)

    assert set(world.rows()) == {1, 2, 3, 4, 5, 6}


# --- the policy --------------------------------------------------------------


def test_the_confirmed_policy_is_365_days_and_four_event_types() -> None:
    policy = history_retention.retention_policy({})

    assert policy.enabled
    assert policy.retention_days == 365
    assert policy.event_types == (
        "login_failed",
        "reauth_failed",
        "user_login",
        "article_word_lookup",
    )


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_the_window_can_be_switched_off(value: str) -> None:
    policy = history_retention.retention_policy({history_retention.RETENTION_DAYS_ENV: value})

    assert not policy.enabled


@pytest.mark.parametrize("value", ["1", "364", "-1", "soon"])
def test_a_shorter_or_unusable_window_is_refused(value: str) -> None:
    with pytest.raises(history_retention.RetentionError):
        history_retention.retention_policy({history_retention.RETENTION_DAYS_ENV: value})


def test_a_longer_window_is_allowed() -> None:
    policy = history_retention.retention_policy({history_retention.RETENTION_DAYS_ENV: "400"})

    assert policy.retention_days == 400
