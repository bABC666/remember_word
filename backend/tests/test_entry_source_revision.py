"""Migration 0010: one evidence column, and the table rebuild that adds it.

``entry_source_evidence`` is the row that ties a displayed value to the physical
source line it came from. A line number without a revision cannot answer "which
version of that page did we use", so 0010 stores the pinned revision the value was
read from.

The interesting part is not the column: SQLite cannot add or drop a constraint in
place, so both directions of this migration rebuild the table. A rebuild is exactly
where a foreign key, a unique key or an index can be silently lost -- and a lost
foreign key is invisible to ``PRAGMA foreign_key_check``, because a constraint that
was never created is never violated. These tests therefore assert the *physical*
shape around the round trip, and prove the constraint from its behaviour rather than
from the DDL text.

Every database here is synthetic: a throwaway file under ``tmp_path``, migrated by
the real Alembic revisions through the same ``run_alembic`` helper the rest of the
suite uses. Nothing in this module touches ``data/``.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from tests.conftest import BACKEND_ROOT, run_alembic

TABLE = "entry_source_evidence"
COLUMN = "source_revision"
CHECK_NAME = "ck_entry_source_evidence_revision_trimmed"

REVISION_0009 = "0009_entry_concise_meaning"
REVISION_0010 = "0010_entry_source_revision"
REVISION_0011 = "0011_entry_concise_meaning_pos"
REVISION_0012 = "0012_source_wikitext_line"

NOW = "2026-01-01 00:00:00"

#: The pinned revision of the word ``admit`` in the zh.wiktionary source, taken from
#: the repository's own pinned list. A realistic value: the constraint is about the
#: shape of a revision identifier, not about the number itself.
PINNED_REVISION = "6588944"


# --- schema introspection -----------------------------------------------------


def connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(database))
    connection.row_factory = sqlite3.Row
    connection.execute("pragma foreign_keys=ON")
    return connection


def column_shape(database: Path, table: str = TABLE) -> list[tuple]:
    """Name, type, NOT NULL and default of every column, in physical order."""
    connection = connect(database)
    try:
        return [
            (row["name"], row["type"], row["notnull"], row["dflt_value"], row["pk"])
            for row in connection.execute(f'pragma table_info("{table}")')
        ]
    finally:
        connection.close()


def foreign_keys(database: Path, table: str = TABLE) -> dict[str, tuple[str, str]]:
    connection = connect(database)
    try:
        return {
            row["from"]: (row["table"], (row["on_delete"] or "NO ACTION").upper())
            for row in connection.execute(f'pragma foreign_key_list("{table}")')
        }
    finally:
        connection.close()


def unique_keys(database: Path, table: str = TABLE) -> set[tuple[tuple[str, ...], bool]]:
    """Every declared unique key as (columns, is_unique), keyed by index content.

    Read from ``index_list``/``index_info`` rather than from ``sqlite_master`` SQL
    text: a rebuild may legitimately re-render the same constraint differently, and
    the question here is whether the constraint still exists, not how it is spelled.
    """
    connection = connect(database)
    try:
        found = set()
        for row in connection.execute(f'pragma index_list("{table}")'):
            columns = tuple(
                item["name"]
                for item in connection.execute(f'pragma index_info("{row["name"]}")')
            )
            found.add((columns, bool(row["unique"])))
        return found
    finally:
        connection.close()


def index_names(database: Path, table: str = TABLE) -> set[str]:
    """Named indexes, excluding the auto-indexes SQLite creates for constraints."""
    connection = connect(database)
    try:
        return {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='index' and tbl_name=? "
                "and name not like 'sqlite_autoindex%'",
                (table,),
            )
        }
    finally:
        connection.close()


def physical_shape(database: Path) -> dict[str, object]:
    """Everything a rebuild could plausibly change about this one table."""
    return {
        "columns": column_shape(database),
        "foreign_keys": foreign_keys(database),
        "unique_keys": unique_keys(database),
        "indexes": index_names(database),
    }


def scalar(database: Path, statement: str, parameters: tuple = ()):
    connection = connect(database)
    try:
        return connection.execute(statement, parameters).fetchone()[0]
    finally:
        connection.close()


def evidence_rows(database: Path) -> list[tuple]:
    connection = connect(database)
    try:
        return [
            tuple(row)
            for row in connection.execute(
                "select id, lexicon_entry_id, source_artifact_id, import_run_id, "
                "normalized_word, row_locator, field_kind, sense_key, raw_word, raw_text, "
                "evidence_sha256, decision, selected_for_default, selection_order, "
                "confirmed_by_username, confirmed_at from entry_source_evidence order by id"
            )
        ]
    finally:
        connection.close()


def check_constraints(database: Path, table: str = TABLE) -> dict[str, str]:
    engine = sa.create_engine(f"sqlite:///{database.as_posix()}")
    try:
        return {
            item["name"]: str(item["sqltext"])
            for item in sa.inspect(engine).get_check_constraints(table)
        }
    finally:
        engine.dispose()


# --- seeding ------------------------------------------------------------------


def first(connection: sqlite3.Connection, statement: str, parameters: tuple = ()):
    row = connection.execute(statement, parameters).fetchone()
    return None if row is None else row[0]


def seed_evidence_row(
    connection: sqlite3.Connection, *, revision: str | None = None, run_tag: str = "1"
) -> int:
    """Insert one complete evidence row with a real chain of parents.

    The insert names its columns explicitly and, when ``revision`` is ``None``, omits
    the 0010 column entirely -- exactly what a pre-0010 writer does. That is how
    "existing rows keep their content" is proved: the row is written the old way and
    read back the old way.

    Parents are **reused** when they already exist, and a fresh ``public_import_run``
    is written on every call. That mirrors the schema's own story: a lexicon, an entry
    and a source artifact are identities, while a later run that re-adjudicates the
    same source position appends its own evidence row under its own run -- which is why
    ``uq_entry_source_evidence`` is keyed on ``(evidence_sha256, import_run_id)`` and
    not on the evidence alone. Creating a second lexicon per call would collide with
    ``lexicon``'s unique ``source_type`` instead of testing anything about revision.
    """
    lexicon_id = first(connection, "select id from lexicon where source_type = 'keepme'")
    if lexicon_id is None:
        connection.execute(
            "insert into lexicon (owner_user_id, name, description, visibility, source_type, "
            "entry_count, created_at, updated_at) values (null, 'keep-lex', '', 'public', "
            "'keepme', 1, ?, ?)",
            (NOW, NOW),
        )
        lexicon_id = connection.execute("select last_insert_rowid()").fetchone()[0]

    entry_id = first(
        connection, "select id from lexicon_entry where normalized_word = 'admit'"
    )
    if entry_id is None:
        connection.execute(
            "insert into lexicon_entry (lexicon_id, word, normalized_word, phonetic, "
            "part_of_speech, source_meanings, source_raw, default_anchor, semantic_note, "
            "possible_issue, created_at, updated_at) values (?, 'admit', 'admit', '', '', "
            "'[\"承认\"]', 'admit v. 承认', '', '', 0, ?, ?)",
            (lexicon_id, NOW, NOW),
        )
        entry_id = connection.execute("select last_insert_rowid()").fetchone()[0]

    artifact_id = first(
        connection, "select id from source_artifact where file_sha256 = ?", ("b" * 64,)
    )
    if artifact_id is None:
        connection.execute(
            "insert into source_artifact (role, name, publisher, version, obtained_at_utc, "
            "format, mapping_json, mapping_sha256, file_sha256, byte_size, license_id, "
            "license_text_sha256, use_scope, display_scope, storage_locator, created_at) "
            "values ('meaning', 'zhwiktionary-v4en.csv', 'zh.wiktionary.org', 'pinned', ?, "
            "'delimited', '{}', ?, ?, 1234, 'CC-BY-SA-4.0', '', 'scope', 'display', 'x', ?)",
            (NOW, "a" * 64, "b" * 64, NOW),
        )
        artifact_id = connection.execute("select last_insert_rowid()").fetchone()[0]

    connection.execute(
        "insert into public_import_run (plan_sha256, run_id, target_lexicon_id, "
        "confirmed_by_username, confirmed_at, status, entries_created, entries_matched, "
        "evidence_written, result_json, error_report_locator, created_at) values (?, ?, ?, "
        "'admin', ?, 'applied', 1, 0, 1, '{}', '', ?)",
        (hashlib.sha256(f"plan-{run_tag}".encode()).hexdigest(), f"lex-run-{run_tag}",
         lexicon_id, NOW, NOW),
    )
    run_id = connection.execute("select last_insert_rowid()").fetchone()[0]

    columns = [
        "lexicon_entry_id", "source_artifact_id", "import_run_id", "normalized_word",
        "row_locator", "field_kind", "sense_key", "raw_word", "raw_text",
        "evidence_sha256", "decision", "selected_for_default", "selection_order",
        "confirmed_by_username", "confirmed_at",
    ]
    values: list[object] = [
        entry_id, artifact_id, run_id, "admit", 2, "meaning", "meaning@2", "admit",
        "承认；准许进入", "d" * 64, "selected", 1, 0, "admin", NOW,
    ]
    if revision is not None:
        columns.append(COLUMN)
        values.append(revision)
    connection.execute(
        f"insert into entry_source_evidence ({', '.join(columns)}) "
        f"values ({', '.join('?' for _ in columns)})",
        tuple(values),
    )
    connection.commit()
    return connection.execute("select last_insert_rowid()").fetchone()[0]


def migrated(tmp_path: Path, revision: str) -> Path:
    """A synthetic database at one revision, built by the real migrations."""
    staging = tmp_path / "app-data" / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    database = staging / "clone.db"
    elsewhere = tmp_path / "app-data" / "declared-real"
    elsewhere.mkdir(exist_ok=True)
    result = run_alembic(
        database, "upgrade", revision, extra_env={"VOCAB_REAL_DATA_DIR": str(elsewhere)}
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return database


def step(tmp_path: Path, database: Path, *arguments: str) -> None:
    elsewhere = tmp_path / "app-data" / "declared-real"
    result = run_alembic(
        database, *arguments, extra_env={"VOCAB_REAL_DATA_DIR": str(elsewhere)}
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


# --- the revision chain -------------------------------------------------------


def test_0010_stacks_on_0009_and_the_graph_has_one_head() -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)

    # ``0011`` and then ``0012`` follow ``0010``, so this asserts the part that is
    # 0010's own: the chain has a single head, and 0010 is a step in it rather than a
    # second branch.
    assert script.get_heads() == [REVISION_0012], (
        "there must be exactly one head; a second head means the migration graph branched"
    )
    assert script.get_revision(REVISION_0011).down_revision == REVISION_0010, (
        "0011 follows 0010 rather than editing it in place"
    )
    assert script.get_revision(REVISION_0010).down_revision == REVISION_0009, (
        "0010 stacks on 0009 rather than editing an unpublished revision in place"
    )
    assert script.get_revision(REVISION_0009).down_revision == "0008_public_lexicon_import"


# --- the round trip -----------------------------------------------------------


def test_0010_adds_one_column_and_its_downgrade_is_exact(tmp_path: Path) -> None:
    """0009 -> 0010 -> 0009 on a table that already holds a row.

    The assertions are about *shape and content together*: the column arrives with
    the right default, the existing row keeps every value it had, and the rebuild
    leaves the foreign keys, the unique key and the indexes where 0008 put them.
    """
    database = migrated(tmp_path, REVISION_0009)

    connection = connect(database)
    try:
        evidence_id = seed_evidence_row(connection)
    finally:
        connection.close()

    before_shape = physical_shape(database)
    before_rows = evidence_rows(database)
    assert [column[0] for column in before_shape["columns"]][-1] != COLUMN

    step(tmp_path, database, "upgrade", REVISION_0010)

    after_shape = physical_shape(database)
    after_columns = {column[0]: column for column in after_shape["columns"]}
    assert COLUMN in after_columns, "0010 must add the revision column"
    _, column_type, not_null, default, _pk = after_columns[COLUMN]
    assert column_type.upper() == "VARCHAR(64)"
    assert not_null == 1, "the column is NOT NULL: 'unknown' is the empty string"
    assert default == "''", "existing rows must backfill to the empty string"

    assert after_shape["foreign_keys"] == before_shape["foreign_keys"], (
        "the rebuild dropped a foreign key, which PRAGMA foreign_key_check cannot "
        f"detect: {before_shape['foreign_keys']} -> {after_shape['foreign_keys']}"
    )
    assert after_shape["unique_keys"] == before_shape["unique_keys"]
    assert after_shape["indexes"] == before_shape["indexes"]
    assert [column[0] for column in after_shape["columns"]][:-1] == [
        column[0] for column in before_shape["columns"]
    ], "the new column is appended, so a migrated table and create_all() agree"

    assert evidence_rows(database) == before_rows, (
        "upgrading must not rewrite any existing value"
    )
    assert scalar(database, f"select {COLUMN} from {TABLE} where id = ?", (evidence_id,)) == ""

    connection = connect(database)
    try:
        assert connection.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert connection.execute("pragma foreign_key_check").fetchall() == []
    finally:
        connection.close()

    step(tmp_path, database, "downgrade", REVISION_0009)

    back_shape = physical_shape(database)
    assert COLUMN not in {column[0] for column in back_shape["columns"]}
    assert back_shape == before_shape, (
        "the downgrade must restore the pre-0010 shape exactly: "
        f"{before_shape} -> {back_shape}"
    )
    assert evidence_rows(database) == before_rows, (
        "the downgrade must not lose or rewrite the evidence row"
    )

    connection = connect(database)
    try:
        assert connection.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert connection.execute("pragma foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_a_row_written_before_0010_is_still_insertable_and_readable(tmp_path: Path) -> None:
    """The column is additive for writers: an old insert keeps working.

    A pre-0010 writer names its columns and never mentions ``source_revision``. If the
    column were nullable-without-default, or had no default, that writer would break --
    so this is really a test of the default, expressed the way the old code behaves.
    """
    database = migrated(tmp_path, REVISION_0010)

    connection = connect(database)
    try:
        evidence_id = seed_evidence_row(connection)  # omits the 0010 column
    finally:
        connection.close()

    assert scalar(
        database, f"select {COLUMN} from {TABLE} where id = ?", (evidence_id,)
    ) == ""

    connection = connect(database)
    try:
        seed_evidence_row(connection, revision=PINNED_REVISION, run_tag="2")
    finally:
        connection.close()
    assert scalar(database, f"select count(*) from {TABLE}") == 2


# --- the constraint -----------------------------------------------------------


@pytest.mark.parametrize("value", [f" {PINNED_REVISION}", f"{PINNED_REVISION} ", "  "])
def test_the_check_rejects_an_untrimmed_revision(tmp_path: Path, value: str) -> None:
    """A revision with surrounding spaces is not the revision.

    A link is built from this value and a re-read of the source has to produce a value
    that compares equal to it. Leading or trailing whitespace would make those two
    differ while looking identical on screen, so the database refuses to store it.
    """
    database = migrated(tmp_path, REVISION_0010)
    connection = connect(database)
    try:
        with pytest.raises(sqlite3.IntegrityError) as error:
            seed_evidence_row(connection, revision=value)
        assert CHECK_NAME in str(error.value), (
            f"the refusal must name the constraint, not some other integrity failure: "
            f"{error.value}"
        )
    finally:
        connection.close()


@pytest.mark.parametrize("value", ["", PINNED_REVISION, "70dc6b68c855f21e666a7a291ff8ead5ca1f7b44"])
def test_the_check_accepts_an_empty_or_clean_revision(tmp_path: Path, value: str) -> None:
    """Empty is legal: it is how a source with no revision, or a row whose cell is
    empty, records "no link"."""
    database = migrated(tmp_path, REVISION_0010)
    connection = connect(database)
    try:
        evidence_id = seed_evidence_row(connection, revision=value)
    finally:
        connection.close()
    assert scalar(
        database, f"select {COLUMN} from {TABLE} where id = ?", (evidence_id,)
    ) == value


def test_the_check_covers_spaces_and_says_so_about_control_characters(tmp_path: Path) -> None:
    """A boundary witness: SQLite's one-argument trim() removes spaces only.

    This asserts the limitation rather than leaving it implied. A tab is *not*
    rejected here, which is deliberate: the column's job is that a stored revision
    compares byte-for-byte with a re-read value, while refusing control characters
    belongs to the validation of the mapping declaration that supplies the value.
    If someone later widens the constraint, this test fails and forces the claim in
    the migration and the model to be updated with it.
    """
    database = migrated(tmp_path, REVISION_0010)
    connection = connect(database)
    try:
        evidence_id = seed_evidence_row(connection, revision=f"\t{PINNED_REVISION}")
    finally:
        connection.close()
    assert scalar(
        database, f"select {COLUMN} from {TABLE} where id = ?", (evidence_id,)
    ) == f"\t{PINNED_REVISION}"


def test_the_named_check_constraint_exists_in_the_migrated_schema(tmp_path: Path) -> None:
    """The constraint is physically present and carries the name 0010 drops by.

    The downgrade identifies the constraint by name, so a schema where it exists
    under a different name would upgrade fine and then fail to downgrade.
    """
    database = migrated(tmp_path, REVISION_0010)
    found = check_constraints(database)
    assert CHECK_NAME in found, f"expected {CHECK_NAME} among {sorted(found)}"
    assert found[CHECK_NAME].replace('"', "") == f"{COLUMN} = trim({COLUMN})"


# --- the model stays in step --------------------------------------------------


def test_the_orm_column_matches_the_migrated_schema(tmp_path: Path) -> None:
    """The model and the migration must describe the same column.

    ``create_all()`` builds test schemas from the ORM while users get their schema
    from the migrations, so a disagreement here would mean the tests pass against a
    schema nobody runs.
    """
    import app.models  # noqa: F401  (populates Base.metadata)
    from app.db import Base

    database = migrated(tmp_path, REVISION_0010)
    table = Base.metadata.tables[TABLE]
    orm_column = table.columns[COLUMN]
    assert orm_column.nullable is False
    assert isinstance(orm_column.type, sa.String)
    assert orm_column.type.length == 64

    physical = {column[0]: column for column in column_shape(database)}
    assert COLUMN in physical
    assert physical[COLUMN][2] == 1, "ORM says NOT NULL, the database must too"

    orm_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert orm_checks.get(CHECK_NAME, "").replace('"', "") == f"{COLUMN} = trim({COLUMN})"
    assert CHECK_NAME in check_constraints(database)
