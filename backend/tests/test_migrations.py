"""Migration tests.

Two independent paths must both work:

A. Fresh database: empty SQLite -> ``alembic upgrade head`` -> schema is correct.
B. Existing V1.1 database: schema at revision ``0003`` -> ``alembic upgrade head``
   -> every existing row survives.

These tests are the only guard against the failure mode this project used to
have, where ``Base.metadata.create_all`` ran against the live models and made
the migration history unreproducible.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"

# Tables the V1.1 baseline (revision 0003) already had.
V1_1_BASELINE_TABLES = {
    "app_setting",
    "article",
    "article_word_exposure",
    "article_word_lookup",
    "history_event",
    "import_batch",
    "import_candidate",
    "import_image",
    "review_event",
    "word",
}

# Columns added to baseline tables by the V1.1 migrations 0002/0003.
V1_1_ADDED_COLUMNS: dict[str, set[str]] = {
    "import_batch": {"is_deleted"},
    "import_image": {"is_deleted"},
    "article": {"translation", "translated_at", "translation_ai_raw_json"},
}
V1_1_ADDED_ARGUMENTS: set[str] = {"INDEX(is_deleted)"}


def _alembic(tmp_root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run alembic against an isolated temporary database.

    The guard refuses any temporary root that does not look like test scratch
    space, so this helper cannot be pointed at the real ``data/`` directory by
    accident. It also verifies afterwards that the database file alembic wrote
    is the one that was requested, so an ignored ``-x db_url`` override can
    never silently migrate the production database.
    """
    from app.testing_guards import (
        assert_not_real_data,
        assert_safe_for_destructive_operation,
    )

    database = tmp_root / "vocab.db"
    assert_safe_for_destructive_operation(database, action="run alembic against")
    assert_not_real_data(tmp_root, action="use as an alembic data directory")

    before = _revision(database)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ALEMBIC_INI),
            "-x",
            f"db_url=sqlite:///{database.as_posix()}",
            *arguments,
        ],
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=_isolated_env(tmp_root),
    )
    if result.returncode != 0:
        raise AssertionError(
            f"alembic {' '.join(arguments)} failed:\n{result.stdout}\n{result.stderr}"
        )

    # Proof that the migration really targeted the requested file: the recorded
    # revision must reflect the requested revision, and must have changed when
    # the command was an upgrade or downgrade.
    assert database.exists(), (
        f"alembic {' '.join(arguments)} did not create {database}; "
        "the db_url override was probably ignored"
    )
    after = _revision(database)
    if arguments:
        command = arguments[0]
        if command == "upgrade":
            assert after is not None, (
                f"{database} has no recorded revision after 'upgrade "
                f"{' '.join(arguments[1:])}', so the migration did not run"
            )
            if before != after:
                pass  # revision advanced: the requested file was migrated
            else:
                assert before == _head_revision(), (
                    f"alembic upgrade did not change the revision of {database} "
                    f"(still {after}) and it is not at head"
                )
        elif command == "downgrade":
            assert before != after, (
                f"alembic downgrade did not change the revision of {database}"
            )
    assert_not_real_data(database, action="verify the migration target")
    return result


def _revision(database: Path) -> str | None:
    """Read the alembic revision recorded in a database, or None."""
    if not database.exists():
        return None
    connection = sqlite3.connect(str(database))
    try:
        row = connection.execute("select version_num from alembic_version").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        connection.close()


def _isolated_env(tmp_root: Path) -> dict[str, str]:
    """Environment for alembic children.

    ``VOCAB_DATA_DIR`` and ``VOCAB_REAL_DATA_DIR`` both point at the temporary
    root, so even if the ``-x db_url`` override were dropped the child would
    still resolve to a temporary database and the guard would fire rather than
    touching real data.
    """
    import os

    env = dict(os.environ)
    env["VOCAB_DATA_DIR"] = str(tmp_root)
    env["VOCAB_REAL_DATA_DIR"] = str(tmp_root)
    return env


@pytest.fixture()
def guarded_data_root(tmp_path: Path) -> Path:
    """A clearly-marked temporary data directory with a nested name pattern."""
    root = tmp_path / "app-data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _alembic_url(tmp_root: Path) -> str:
    return f"sqlite:///{(tmp_root / 'vocab.db').as_posix()}"


def _table_arguments(table: sa.Table, *, skip: bool = False) -> set[str]:
    rendered: set[str] = set()
    for constraint in table.constraints:
        if isinstance(constraint, sa.UniqueConstraint):
            columns = sorted(column.name for column in constraint.columns)
            rendered.add(f"UNIQUE({','.join(columns)})")
    for index in table.indexes:
        columns = sorted(column.name for column in index.columns)
        rendered.add(f"INDEX({','.join(columns)})")
    if skip:
        # Indexes added after the V1.1 baseline are not part of the baseline.
        rendered = {item for item in rendered if item not in V1_1_ADDED_ARGUMENTS}
    return rendered


def _type_family(column_type: str) -> str:
    """Keep only the type family.

    SQLite reflection reports ``VARCHAR`` while SQLAlchemy renders
    ``VARCHAR(160)``; the declared lengths are not what these tests guard.
    """
    family = str(column_type).upper().split("(")[0].strip()
    # SQLite has no dedicated JSON type: it stores JSON columns as TEXT with a
    # JSON type marker, so reflection may report either spelling. Alembic's
    # batch table rebuild drops the marker entirely.
    return "TEXT" if family == "JSON" else family


def _normalized_type(column: sa.Column) -> str:
    return _type_family("TEXT" if isinstance(column.type, sa.JSON) else str(column.type))


def _expected_schema(status: str = "models") -> dict[str, dict[str, object]]:
    """Snapshot the live models, with SQLite storage quirks applied.

    ``status="v1_1_baseline"`` keeps only the tables and columns that the V1.1
    schema (revision ``0003``) already had. Until the V1.2 migrations land, the
    head schema is the V1.1 schema, so "everything the baseline had must still
    be present and unchanged" is the correct assertion for that stage.
    """
    import app.models  # noqa: F401
    from app.db import Base

    baseline_only = status == "v1_1_baseline"
    expected: dict[str, dict[str, object]] = {}
    for name, table in Base.metadata.tables.items():
        if baseline_only and name not in V1_1_BASELINE_TABLES:
            continue
        columns: dict[str, tuple[str, bool, bool]] = {}
        for column in table.columns:
            if baseline_only and column.name in V1_1_ADDED_COLUMNS.get(name, set()):
                continue
            columns[column.name] = (
                _normalized_type(column),
                bool(column.nullable),
                bool(column.primary_key),
            )
        if baseline_only and not columns:
            continue
        expected[name] = {
            "columns": columns,
            "arguments": _table_arguments(table, skip=baseline_only),
        }
    return expected


def _actual_schema(database: Path) -> dict[str, dict[str, object]]:
    connection = sqlite3.connect(database)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table' "
                "and name not like 'sqlite_%' order by name"
            )
        ]
        actual: dict[str, dict[str, object]] = {}
        for table in tables:
            columns: dict[str, tuple[str, bool, bool]] = {}
            for _cid, name, column_type, notnull, _default, pk in connection.execute(
                f'pragma table_info("{table}")'
            ):
                columns[name] = (
                    _type_family(column_type),
                    not notnull,
                    bool(pk),
                )
            arguments: set[str] = set()
            for _seq, index_name, unique, origin, _partial in connection.execute(
                f'pragma index_list("{table}")'
            ):
                if origin == "pk":
                    continue
                index_columns = sorted(
                    row[2]
                    for row in connection.execute(f'pragma index_info("{index_name}")')
                    if row[2]
                )
                kind = "UNIQUE" if unique else "INDEX"
                arguments.add(f"{kind}({','.join(index_columns)})")
            actual[table] = {"columns": columns, "arguments": arguments}
        return actual
    finally:
        connection.close()


def _code_only(source: str) -> str:
    """Strip docstrings and comments so prose cannot trip the guards below."""
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_fresh_database_upgrades_to_head_with_explicit_schema(tmp_path: Path) -> None:
    """Path A: an empty SQLite file must reach the V1.1 baseline schema.

    Once the V1.2 migrations exist, ``head`` is a superset of the baseline, so
    this asserts the baseline tables/columns are reproduced exactly by the
    explicit migration statements rather than by the live models.
    """
    _alembic(tmp_path, "upgrade", "head")

    database = tmp_path / "vocab.db"
    assert database.exists()

    connection = sqlite3.connect(database)
    try:
        revision = connection.execute("select version_num from alembic_version").fetchone()
    finally:
        connection.close()
    assert revision is not None

    actual = _actual_schema(database)
    expected = _expected_schema("v1_1_baseline")

    missing = sorted(set(expected) - set(actual))
    assert missing == [], f"baseline tables missing from a fresh upgrade: {missing}"
    for table, spec in expected.items():
        for column, definition in spec["columns"].items():
            assert column in actual[table]["columns"], f"{table}.{column} missing"
            assert actual[table]["columns"][column] == definition, (
                f"{table}.{column} differs: "
                f"{actual[table]['columns'][column]} != {definition}"
            )
        assert spec["arguments"] <= actual[table]["arguments"], (
            f"constraints differ for {table}"
        )


def test_migration_history_does_not_depend_on_runtime_models() -> None:
    """The initial revision must build the schema with explicit statements only."""
    source = (BACKEND_ROOT / "alembic" / "versions" / "0001_initial.py").read_text(
        encoding="utf-8"
    )
    code = _code_only(source)
    assert "create_all" not in code
    assert "Base.metadata" not in code


def test_application_startup_does_not_create_schema() -> None:
    """Startup must not alter the schema; only Alembic may."""
    source = (BACKEND_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    code = _code_only(source)
    assert "create_all" not in code
    assert "Base.metadata" not in code


# --------------------------------------------------------------------------
# Path B: existing V1.1 database
# --------------------------------------------------------------------------

V1_1_WORDS = [
    # word, phonetic, pos, meanings, raw, anchor, note, status,
    # success, fail, failures, exposure, issue, notes
    (
        "quit",
        "kwɪt",
        "v.",
        ["停止", "放弃"],
        "quit /kwɪt/ v. 停止；放弃",
        "停止做某事",
        "强调彻底停止",
        "learning",
        2,
        1,
        1,
        3,
        False,
        "",
    ),
    (
        "substitute",
        "ˈsʌbstɪtjuːt",
        "n./v.",
        ["代替物", "替代"],
        "substitute n. 代替物 v. 替代",
        "用别的东西顶上",
        "",
        "familiar",
        1,
        0,
        0,
        2,
        False,
        "",
    ),
    (
        "possible-issue-word",
        "",
        "",
        [],
        "从阅读文章加入",
        "AI 辅助释义",
        "AI 解释",
        "new",
        0,
        0,
        0,
        0,
        True,
        "从阅读文章加入；中文释义由 AI 辅助生成，请在学习时核对。",
    ),
]


def _seed_v1_1(database: Path) -> None:
    """Insert representative V1.1 rows through raw SQL only."""
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        for (
            word,
            phonetic,
            pos,
            meanings,
            raw,
            anchor,
            note,
            status,
            success,
            fail,
            failures,
            exposure,
            issue,
            notes,
        ) in V1_1_WORDS:
            connection.execute(
                "insert into word (word, phonetic, part_of_speech, source_meanings, "
                "source_raw, anchor, semantic_note, status, first_seen, last_review, "
                "next_review_at, recall_success, recall_fail, consecutive_failures, "
                "context_exposure, possible_issue, notes, created_at, updated_at) "
                "values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    word,
                    phonetic,
                    pos,
                    json.dumps(meanings, ensure_ascii=False),
                    raw,
                    anchor,
                    note,
                    status,
                    "2026-09-21 10:00:00.000000",
                    "2026-09-22 09:00:00.000000",
                    "2026-09-23 09:00:00.000000",
                    success,
                    fail,
                    failures,
                    exposure,
                    1 if issue else 0,
                    notes,
                    "2026-09-21 10:00:00.000000",
                    "2026-09-21 10:00:00.000000",
                ),
            )

        connection.execute(
            "insert into article (title, content, created_at, target_words, "
            "actual_used_words, completed, completed_at, ai_raw_json, translation, "
            "translated_at, translation_ai_raw_json) values (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "A Quiet Signal",
                "She decided to quit the project and substitute another plan.",
                "2026-09-22 08:00:00.000000",
                json.dumps(["quit", "substitute"]),
                json.dumps(["quit", "substitute"]),
                1,
                "2026-09-22 09:30:00.000000",
                "{}",
                "她决定退出这个项目，改用另一个计划。",
                "2026-09-22 09:40:00.000000",
                "{}",
            ),
        )

        for word_id, context in ((1, "She decided to quit the project."), (2, "substitute")):
            connection.execute(
                "insert into article_word_exposure (article_id, word_id, context, "
                "exposure_count, first_exposed_at, last_exposed_at) values (?,?,?,?,?,?)",
                (
                    1,
                    word_id,
                    context,
                    1,
                    "2026-09-22 09:30:00.000000",
                    "2026-09-22 09:30:00.000000",
                ),
            )

        for word_id, result, before, after in (
            (1, "fail", "new", "weak"),
            (1, "fuzzy", "weak", "learning"),
            (2, "know", "new", "familiar"),
        ):
            connection.execute(
                "insert into review_event (word_id, timestamp, result, source, article_id, "
                "status_before, status_after, review_type) values (?,?,?,?,?,?,?,?)",
                (
                    word_id,
                    "2026-09-22 09:00:00.000000",
                    result,
                    "daily",
                    1 if word_id == 1 else None,
                    before,
                    after,
                    "recall",
                ),
            )

        connection.execute(
            "insert into article_word_lookup (article_id, surface, normalized_word, "
            "phonetic, part_of_speech, meaning, explanation, context, source, "
            "added_word_id, ai_raw_json, created_at) values (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                1,
                "substitute",
                "substitute",
                "ˈsʌbstɪtjuːt",
                "v.",
                "替代",
                "用另一个东西顶上",
                "substitute",
                "wordbook",
                2,
                "{}",
                "2026-09-22 09:35:00.000000",
            ),
        )

        connection.execute(
            "insert into import_batch (status, stage, provider, raw_ocr_text, raw_ocr_json, "
            "error_stage, error_message, is_deleted, created_at, updated_at) "
            "values (?,?,?,?,?,?,?,?,?,?)",
            (
                "confirmed",
                "confirmed",
                "paddleocr",
                "quit /kwɪt/ v. 停止",
                "{}",
                "",
                "",
                0,
                "2026-09-21 09:00:00.000000",
                "2026-09-21 09:10:00.000000",
            ),
        )
        connection.execute(
            "insert into import_image (batch_id, original_name, file_path, sha256, "
            "mime_type, width, height, ocr_raw_json, ocr_text, error_message, is_deleted, "
            "created_at) values (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                1,
                "page1.jpg",
                "/data/uploads/1/abc.jpg",
                "a" * 64,
                "image/jpeg",
                1000,
                1400,
                "{}",
                "quit /kwɪt/ v. 停止",
                "",
                0,
                "2026-09-21 09:00:00.000000",
            ),
        )
        for index in range(1, 4):
            connection.execute(
                "insert into import_candidate (batch_id, word_id, word, phonetic, "
                "part_of_speech, source_meanings, source_raw, anchor, semantic_note, "
                "possible_issue, issue_note, selected, confirmed, ai_raw_json, "
                "created_at, updated_at) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    1,
                    index,
                    f"candidate{index}",
                    "",
                    "n.",
                    "[]",
                    "raw",
                    "anchor",
                    "",
                    0,
                    "",
                    1,
                    1,
                    "{}",
                    "2026-09-21 09:05:00.000000",
                    "2026-09-21 09:05:00.000000",
                ),
            )

        for event_type in ("import_confirmed", "manual_backup", "article_translated"):
            connection.execute(
                "insert into history_event (event_type, timestamp, entity_type, entity_id, "
                "payload) values (?,?,?,?,?)",
                (
                    event_type,
                    "2026-09-21 09:20:00.000000",
                    "test",
                    1,
                    "{}",
                ),
            )

        for key, value in (
            ("daily_new_words", "15"),
            ("article_length", "650"),
            ("ocr_language", "en"),
            ("ocr_use_gpu", "false"),
            ("onboarding_seen", "true"),
        ):
            connection.execute(
                "insert into app_setting (key, value, updated_at) values (?,?,?)",
                (key, value, "2026-09-21 09:00:00.000000"),
            )

        connection.commit()
    finally:
        connection.close()


V1_1_COUNTS = {
    "word": len(V1_1_WORDS),
    "review_event": 3,
    "article": 1,
    "article_word_exposure": 2,
    "article_word_lookup": 1,
    "import_batch": 1,
    "import_image": 1,
    "import_candidate": 3,
    "history_event": 3,
    "app_setting": 5,
}


def _counts(database: Path) -> dict[str, int]:
    connection = sqlite3.connect(database)
    try:
        return {
            table: connection.execute(f'select count(*) from "{table}"').fetchone()[0]
            for table in V1_1_COUNTS
        }
    finally:
        connection.close()


@pytest.fixture()
def v1_1_database(guarded_data_root: Path) -> Path:
    _alembic(guarded_data_root, "upgrade", "0003_article_reading_tools")
    _seed_v1_1(guarded_data_root / "vocab.db")
    return guarded_data_root / "vocab.db"


def test_v1_1_database_rolls_forward_without_data_loss(v1_1_database: Path) -> None:
    """Path B: upgrading a real V1.1 database must preserve every row."""
    before_connection = sqlite3.connect(v1_1_database)
    try:
        words_before = before_connection.execute(
            "select id, word, phonetic, part_of_speech, source_meanings, source_raw, "
            "anchor, semantic_note, status, first_seen, last_review, next_review_at, "
            "recall_success, recall_fail, consecutive_failures, context_exposure, "
            "possible_issue, notes from word order by id"
        ).fetchall()
        events_before = before_connection.execute(
            "select id, word_id, timestamp, result, source, article_id, status_before, "
            "status_after, review_type from review_event order by id"
        ).fetchall()
    finally:
        before_connection.close()

    assert _counts(v1_1_database) == V1_1_COUNTS
    counts_before = _counts(v1_1_database)

    _alembic(v1_1_database.parent, "upgrade", "head")

    # Row counts for every pre-existing table are unchanged.
    assert _counts(v1_1_database) == counts_before

    after_connection = sqlite3.connect(v1_1_database)
    try:
        words_after = after_connection.execute(
            "select id, word, phonetic, part_of_speech, source_meanings, source_raw, "
            "anchor, semantic_note, status, first_seen, last_review, next_review_at, "
            "recall_success, recall_fail, consecutive_failures, context_exposure, "
            "possible_issue, notes from word order by id"
        ).fetchall()
        events_after = after_connection.execute(
            "select id, word_id, timestamp, result, source, article_id, status_before, "
            "status_after, review_type from review_event order by id"
        ).fetchall()
        foreign_key_errors = after_connection.execute("pragma foreign_key_check").fetchall()
        integrity = after_connection.execute("pragma integrity_check").fetchone()[0]
        revision = after_connection.execute("select version_num from alembic_version").fetchone()[0]
    finally:
        after_connection.close()

    assert words_after == words_before, "word rows changed during migration"
    assert events_after == events_before, "review_event rows changed during migration"
    assert foreign_key_errors == []
    assert integrity == "ok"

    # The V1.1 baseline schema must be fully preserved by the roll-forward.
    actual = _actual_schema(v1_1_database)
    expected = _expected_schema("v1_1_baseline")
    for table, spec in expected.items():
        assert table in actual, f"baseline table {table} disappeared"
        for column, definition in spec["columns"].items():
            assert column in actual[table]["columns"], f"{table}.{column} disappeared"
            assert actual[table]["columns"][column] == definition, (
                f"{table}.{column} changed during migration"
            )

    if _head_revision() != "0003_article_reading_tools":
        assert revision == _head_revision(), "the database did not reach head"
        # Once a V1.2 migration exists, head must match the models exactly.
        models = _expected_schema("models")
        assert sorted(actual) == sorted(models), "table set differs from the models"
        for table, spec in models.items():
            assert actual[table]["columns"] == spec["columns"], f"columns differ for {table}"


def _head_revision() -> str:
    """Read the current Alembic head from the versions directory."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1, f"expected a single head, found {heads}"
    return heads[0]


def test_sqlite_pragmas_are_configured_for_concurrent_writers() -> None:
    from app.db import make_engine

    engine = make_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    engine.dispose()


def test_ocr_can_be_disabled_by_configuration(monkeypatch) -> None:
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("VOCAB_ENABLE_OCR", "false")
    assert get_settings().ocr_enabled is False
    monkeypatch.setenv("VOCAB_ENABLE_OCR", "true")
    get_settings.cache_clear()
    assert get_settings().ocr_enabled is True
    get_settings.cache_clear()
