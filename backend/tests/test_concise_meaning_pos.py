"""Migration 0011: grouping each short meaning under a part of speech.

``entry_concise_meaning`` used to hold "one to three short senses" per **word**, with
one live row per ``(entry, display_order)``. The product rule is one to three per
**part of speech**, and the twenty-word trial record shows why the difference is not
cosmetic: nine of the twenty words have two parts of speech and ``play`` carries four
values in total. That shape cannot be stored in three slots, so 0011 widens the slot to
``(entry, pos_key, display_order)``, records where each part of speech came from, and
adds a table for the additional source positions a value may rest on.

Three things shape these tests:

* **The database is the enforcer.** Every rule below is asserted by attempting a write
  that must be refused, not by reading a Python constant back. A rule that only the
  service honours is a rule a later code path can skip.
* **A rebuild is where constraints vanish.** SQLite adds a CHECK only by rebuilding the
  table, and a lost foreign key is invisible to ``PRAGMA foreign_key_check`` because a
  constraint that was never created is never violated. The inherited CHECKs from 0009
  are the most fragile of all: SQLAlchemy reflects SQLite CHECK constraints by parsing
  the DDL, so 0011 asserts they survived instead of trusting that they did.
* **Nothing is guessed.** The upgrade refuses -- rather than backfills -- when a
  database already holds a *confirmed* value, because the delivered source maps no
  part-of-speech column at all and any value chosen for those rows would be a machine
  guess stored in the shape of a fact.

Every database here is synthetic: a throwaway file under ``tmp_path``, migrated by the
real revisions through the same ``run_alembic`` helper the rest of the suite uses.
Nothing in this module touches ``data/``.
"""

from __future__ import annotations

import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from tests.conftest import BACKEND_ROOT, run_alembic

REVISION_0010 = "0010_entry_source_revision"
REVISION_0011 = "0011_entry_concise_meaning_pos"
REVISION_0012 = "0012_source_wikitext_line"

MEANING = "entry_concise_meaning"
HISTORY = "entry_concise_meaning_revision"
CITATION = "entry_concise_meaning_citation"
SLOT_INDEX = "uq_entry_concise_meaning_slot"

NOW = "2026-01-01 00:00:00"

#: The nine CHECK constraints 0009 created on ``entry_concise_meaning``. If a rebuild
#: drops one, a product rule stops being enforced by the database and nothing else
#: notices -- which is why they are named individually rather than counted.
INHERITED_MEANING_CHECKS = frozenset(
    {
        "ck_entry_concise_meaning_text_present",
        "ck_entry_concise_meaning_text_short",
        "ck_entry_concise_meaning_order_range",
        "ck_entry_concise_meaning_kind",
        "ck_entry_concise_meaning_status",
        "ck_entry_concise_meaning_locator_for_source",
        "ck_entry_concise_meaning_supplement_has_no_source",
        "ck_entry_concise_meaning_note_when_not_verbatim",
        "ck_entry_concise_meaning_confirmed_is_attributed",
    }
)

NEW_MEANING_CHECKS = frozenset(
    {
        "ck_entry_concise_meaning_pos_key",
        "ck_entry_concise_meaning_pos_source",
        "ck_entry_concise_meaning_pos_key_trimmed",
        "ck_entry_concise_meaning_pos_key_matches_source",
        "ck_entry_concise_meaning_pos_evidence",
        "ck_entry_concise_meaning_pos_order_positive",
        "ck_entry_concise_meaning_language",
        "ck_entry_concise_meaning_language_trimmed",
    }
)

CITATION_CHECKS = frozenset(
    {
        "ck_entry_concise_meaning_citation_order_positive",
        "ck_entry_concise_meaning_citation_locator_present",
        "ck_entry_concise_meaning_citation_locator_trimmed",
    }
)


# --- building and reading a synthetic database --------------------------------


def staging(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A staging-shaped scratch path plus the env that marks it disposable."""
    scratch = tmp_path / "app-data" / "staging"
    scratch.mkdir(parents=True, exist_ok=True)
    elsewhere = tmp_path / "app-data" / "declared-real"
    elsewhere.mkdir(parents=True, exist_ok=True)
    return scratch / "clone.db", {"VOCAB_REAL_DATA_DIR": str(elsewhere)}


def migrated(tmp_path: Path, revision: str = "head") -> Path:
    database, env = staging(tmp_path)
    result = run_alembic(database, "upgrade", revision, extra_env=env)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return database


def step(tmp_path: Path, database: Path, direction: str, revision: str):
    _unused, env = staging(tmp_path)
    return run_alembic(database, direction, revision, extra_env=env)


def connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(database))
    connection.row_factory = sqlite3.Row
    connection.execute("pragma foreign_keys=ON")
    return connection


def columns(database: Path, table: str = MEANING) -> list[str]:
    connection = connect(database)
    try:
        return [row["name"] for row in connection.execute(f'pragma table_info("{table}")')]
    finally:
        connection.close()


def column_details(database: Path, table: str = MEANING) -> dict[str, tuple]:
    connection = connect(database)
    try:
        return {
            row["name"]: (row["type"], row["notnull"], row["dflt_value"])
            for row in connection.execute(f'pragma table_info("{table}")')
        }
    finally:
        connection.close()


def foreign_keys(database: Path, table: str = MEANING) -> dict[str, tuple[str, str]]:
    connection = connect(database)
    try:
        return {
            row["from"]: (row["table"], (row["on_delete"] or "NO ACTION").upper())
            for row in connection.execute(f'pragma foreign_key_list("{table}")')
        }
    finally:
        connection.close()


def index_names(database: Path, table: str = MEANING) -> set[str]:
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


def check_names(database: Path, table: str) -> set[str]:
    """Named CHECK constraints, via SQLAlchemy's own SQLite reflection.

    Deliberately not the migration's helper: this is an independent read, so a bug in
    the migration's own verification cannot hide a dropped constraint from the test.
    """
    engine = sa.create_engine(f"sqlite:///{Path(database).as_posix()}")
    try:
        return {
            item["name"] for item in sa.inspect(engine).get_check_constraints(table)
        }
    finally:
        engine.dispose()


def table_names(database: Path) -> set[str]:
    connection = connect(database)
    try:
        return {
            row[0]
            for row in connection.execute("select name from sqlite_master where type='table'")
        }
    finally:
        connection.close()


def foreign_key_count(database: Path) -> int:
    return sum(
        len(foreign_keys(database, table))
        for table in table_names(database)
        if not table.startswith("sqlite_")
    )


def alembic_revision(database: Path) -> str:
    connection = connect(database)
    try:
        return connection.execute("select version_num from alembic_version").fetchone()[0]
    finally:
        connection.close()


def index_columns(database: Path, index: str) -> tuple[str, ...]:
    connection = connect(database)
    try:
        return tuple(
            row["name"] for row in connection.execute(f'pragma index_info("{index}")')
        )
    finally:
        connection.close()


def integrity(database: Path) -> tuple[str, list]:
    connection = connect(database)
    try:
        return (
            connection.execute("pragma integrity_check").fetchone()[0],
            connection.execute("pragma foreign_key_check").fetchall(),
        )
    finally:
        connection.close()


# --- seeding ------------------------------------------------------------------


def seed_lexicon_and_entry(database: Path, word: str = "prior") -> tuple[int, int]:
    """The minimum a concise meaning needs: one lexicon and one entry."""
    connection = connect(database)
    try:
        connection.execute(
            "insert into lexicon (owner_user_id, name, description, visibility, "
            "source_type, entry_count, created_at, updated_at) values (null, 'keep', '', "
            "'public', 'keepme', 1, ?, ?)",
            (NOW, NOW),
        )
        lexicon_id = connection.execute("select last_insert_rowid()").fetchone()[0]
        connection.execute(
            "insert into lexicon_entry (lexicon_id, word, normalized_word, phonetic, "
            "part_of_speech, source_meanings, source_raw, default_anchor, semantic_note, "
            "possible_issue, frequency_source, created_at, updated_at) values "
            "(?, ?, ?, '', '', '[\"先前的\"]', 'prior adj. 先的，前的', '', '', 0, '', ?, ?)",
            (lexicon_id, word, word.casefold(), NOW, NOW),
        )
        entry_id = connection.execute("select last_insert_rowid()").fetchone()[0]
        connection.commit()
        return lexicon_id, entry_id
    finally:
        connection.close()


def hexish(tag: str) -> str:
    """A 64-character hex string, for the sha256-shaped columns."""
    return ((tag.encode("utf-8").hex() or "00") * 64)[:64]


def seed_evidence(database: Path, entry_id: int, *, locator: int, text: str, tag: str) -> int:
    """One evidence row with a real chain of parents, for citation tests."""
    connection = connect(database)
    try:
        # ``source_artifact`` carries its own provenance CHECK: a publisher, version,
        # obtain time, licence id and both scopes must all be present. A blank one is a
        # source nobody could ever re-check, which is what that constraint is for.
        connection.execute(
            "insert into source_artifact (role, name, publisher, version, "
            "obtained_at_utc, format, mapping_json, mapping_sha256, file_sha256, "
            "byte_size, license_id, license_text_sha256, use_scope, display_scope, "
            "storage_locator, created_at) values ('meaning', ?, 'synthetic', 'v1', "
            "'2026-01-01T00:00:00Z', 'csv', '{}', ?, ?, 1, 'CC-BY-SA-4.0', ?, "
            "'synthetic test scope', 'synthetic test scope', 'nowhere', ?)",
            (f"artifact-{tag}", hexish(tag), hexish(f"f{tag}"), hexish(f"l{tag}"), NOW),
        )
        artifact_id = connection.execute("select last_insert_rowid()").fetchone()[0]
        connection.execute(
            "insert into public_import_run (plan_sha256, run_id, target_lexicon_id, "
            "confirmed_by_username, confirmed_at, status, entries_created, "
            "entries_matched, evidence_written, result_json, error_report_locator, "
            "created_at) values (?, ?, (select lexicon_id from lexicon_entry where id = ?), "
            "'owner', ?, 'confirmed', 1, 0, 1, '{}', '', ?)",
            (hexish(f"p{tag}"), f"run-{tag}", entry_id, NOW, NOW),
        )
        run_id = connection.execute("select last_insert_rowid()").fetchone()[0]
        connection.execute(
            "insert into entry_source_evidence (lexicon_entry_id, source_artifact_id, "
            "import_run_id, normalized_word, row_locator, field_kind, sense_key, raw_word, "
            "raw_text, evidence_sha256, decision, selected_for_default, "
            "confirmed_by_username, confirmed_at, source_revision) values "
            "(?, ?, ?, 'prior', ?, 'meaning', ?, 'prior', ?, ?, 'selected', 1, "
            "'owner', ?, '9576029')",
            (entry_id, artifact_id, run_id, locator, f"meaning@{locator}", text,
             hexish(f"e{tag}"), NOW),
        )
        evidence_id = connection.execute("select last_insert_rowid()").fetchone()[0]
        connection.commit()
        return evidence_id
    finally:
        connection.close()


def insert_meaning(
    database: Path,
    entry_id: int,
    *,
    order: int = 1,
    text: str = "先前的",
    status: str = "candidate",
    kind: str = "derived",
    locator: str = "zhwiktionary:9576029:15",
    note: str = "由「先的／前的」改写",
    pos_key: str = "",
    pos_order: int = 1,
    pos_source: str = "none",
    pos_evidence: str = "",
    language: str = "",
    confirmed: bool = False,
    evidence_id: int | None = None,
) -> int:
    """Insert one display value. Raises whatever SQLite raises, on purpose.

    On a database that has not reached 0011 the insert names **only the pre-0011
    columns**, exactly as a writer from before this revision does. That is what makes
    "an existing row keeps its content through the upgrade" a real claim rather than one
    that quietly relies on the new columns already being there.
    """
    legacy = "pos_key" not in columns(database)
    connection = connect(database)
    try:
        if legacy:
            connection.execute(
                "insert into entry_concise_meaning (lexicon_entry_id, display_order, text, "
                "provenance_kind, source_evidence_id, source_locator, derivation_note, status, "
                "proposed_by_username, proposed_at, confirmed_by_username, confirmed_at, "
                "created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, 'owner', ?, "
                "?, ?, ?, ?)",
                (
                    entry_id, order, text, kind, evidence_id, locator, note, status, NOW,
                    "owner" if confirmed else "", NOW if confirmed else None, NOW, NOW,
                ),
            )
        else:
            connection.execute(
                "insert into entry_concise_meaning (lexicon_entry_id, display_order, text, "
                "provenance_kind, source_evidence_id, source_locator, derivation_note, status, "
                "proposed_by_username, proposed_at, confirmed_by_username, confirmed_at, "
                "created_at, updated_at, pos_key, pos_label, pos_order, pos_source, "
                "pos_evidence_locator, language) values (?, ?, ?, ?, ?, ?, ?, ?, 'owner', ?, "
                "?, ?, ?, ?, ?, '', ?, ?, ?, ?)",
                (
                    entry_id, order, text, kind, evidence_id, locator, note, status, NOW,
                    "owner" if confirmed else "", NOW if confirmed else None, NOW, NOW,
                    pos_key, pos_order, pos_source, pos_evidence, language,
                ),
            )
        meaning_id = connection.execute("select last_insert_rowid()").fetchone()[0]
        connection.commit()
        return meaning_id
    finally:
        connection.close()


def insert_citation(
    database: Path,
    meaning_id: int,
    *,
    order: int = 1,
    locator: str = "wikdict:37",
    evidence_id: int | None = None,
) -> int:
    connection = connect(database)
    try:
        connection.execute(
            "insert into entry_concise_meaning_citation (concise_meaning_id, "
            "citation_order, citation_locator, source_evidence_id, created_at) "
            "values (?, ?, ?, ?, ?)",
            (meaning_id, order, locator, evidence_id, NOW),
        )
        citation_id = connection.execute("select last_insert_rowid()").fetchone()[0]
        connection.commit()
        return citation_id
    finally:
        connection.close()


def refused(database: Path, action) -> str:
    """Run a write that must be refused, and hand back the engine's message."""
    with pytest.raises(sqlite3.IntegrityError) as error:
        action()
    return str(error.value)


# --- the chain ----------------------------------------------------------------


def test_0011_stacks_on_0010_and_the_graph_has_one_head() -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)

    assert len(script.get_heads()) == 1, "the migration graph must have one head"
    assert script.get_revision(REVISION_0011).down_revision == REVISION_0010, (
        "0011 follows 0010 rather than editing it in place"
    )


def load_migration():
    """The 0011 module itself, loaded by path.

    ``alembic/versions`` is not an importable package -- Alembic finds revisions through
    its own script directory -- so the file is loaded directly rather than imported.
    """
    import importlib.util

    path = BACKEND_ROOT / "alembic" / "versions" / "0011_entry_concise_meaning_pos.py"
    spec = importlib.util.spec_from_file_location("migration_0011", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_vocabulary_matches_the_models() -> None:
    """A value legal in Python and illegal in the database is a production-only bug.

    The migration spells its vocabulary out as literals -- a revision has to describe
    the shape it created even after the models move on -- so the two are compared here,
    where a drift shows up as a failing test rather than as a refused write.
    """
    migration = load_migration()
    from app import models

    assert migration.POS_KEYS == models.CONCISE_MEANING_POS_KEYS
    assert migration.LANGUAGES == models.CONCISE_MEANING_LANGUAGES
    assert migration.POS_KEY_SQL == models.CONCISE_MEANING_POS_KEY_SQL
    assert migration.LANGUAGE_SQL == models.CONCISE_MEANING_LANGUAGE_SQL
    assert migration.POS_SOURCE_SQL == models.CONCISE_MEANING_POS_SOURCE_SQL


# --- the upgrade and its reverse ----------------------------------------------


def test_0011_appends_columns_and_keeps_every_inherited_constraint(tmp_path: Path) -> None:
    """0010 -> 0011 on a database that already holds rows.

    The assertions are about shape and content together: the columns arrive with the
    right defaults, the existing row keeps every value, and the rebuild carries the
    foreign keys, the unique key, the indexes **and the nine CHECK constraints of 0009**
    through to the other side.
    """
    database = migrated(tmp_path, REVISION_0010)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)
    insert_meaning(database, entry_id, text="原值", kind="source", note="")

    before_columns = columns(database)
    before_row = read_meaning(database, entry_id)

    assert set(check_names(database, MEANING)) == INHERITED_MEANING_CHECKS

    step(tmp_path, database, "upgrade", REVISION_0011)

    after_columns = columns(database)
    assert after_columns[: len(before_columns)] == before_columns, (
        "the new columns are appended, so a migrated table and create_all() agree"
    )
    assert after_columns[len(before_columns):] == [
        "pos_key",
        "pos_label",
        "pos_order",
        "pos_source",
        "pos_evidence_locator",
        "language",
    ]

    details = column_details(database)
    for name, expected_default in (
        ("pos_key", "''"),
        ("pos_label", "''"),
        # SQLite reports an unquoted numeric default back without quotes, which is the
        # point: a quoted default on an INTEGER column states the wrong type.
        ("pos_order", "1"),
        ("pos_source", "'none'"),
        ("pos_evidence_locator", "''"),
        ("language", "''"),
    ):
        _type, not_null, default = details[name]
        assert not_null == 1, f"{name} must be NOT NULL: 'unknown' is not a NULL here"
        assert default == expected_default, f"{name} default is {default!r}"

    assert check_names(database, MEANING) == INHERITED_MEANING_CHECKS | NEW_MEANING_CHECKS, (
        "the rebuild dropped a CHECK constraint, which no pragma would report"
    )

    assert read_meaning(database, entry_id) == before_row, (
        "upgrading must not rewrite any existing value"
    )
    assert index_columns(database, SLOT_INDEX) == (
        "lexicon_entry_id",
        "pos_key",
        "display_order",
    )
    assert integrity(database) == ("ok", [])
    assert alembic_revision(database) == REVISION_0011


def test_the_downgrade_restores_the_pre_0011_shape(tmp_path: Path) -> None:
    # Started at 0011 rather than at head: the claim is about *this* step, and a
    # multi-step downgrade from a later revision would perform that revision's own
    # downgrade first (harmlessly, but then it would not be this step's shape that the
    # assertions below measure).
    database = migrated(tmp_path, REVISION_0011)
    assert foreign_key_count(database) == 42, (
        "0011 adds exactly one table carrying two foreign keys, and changes no other "
        "table's own count"
    )

    step(tmp_path, database, "downgrade", REVISION_0010)

    assert "pos_key" not in columns(database)
    assert CITATION not in table_names(database)
    assert index_columns(database, SLOT_INDEX) == ("lexicon_entry_id", "display_order")
    assert check_names(database, MEANING) == INHERITED_MEANING_CHECKS, (
        "the downgrade must leave 0009's constraints, not remove the table's checks"
    )
    assert foreign_key_count(database) == 40
    assert integrity(database) == ("ok", [])
    assert alembic_revision(database) == REVISION_0010


def assert_refused_without_changes(tmp_path: Path, database: Path, *reasons: str) -> None:
    """A refusal must happen before SQLite sees any destructive DDL.

    The database has to be sitting at ``0011``: a downgrade command from a later
    revision runs that revision's own downgrade first, so a "nothing moved" claim about
    the 0011 step can only be made against a database whose head *is* 0011.
    """
    before_bytes = sha256(database.read_bytes()).hexdigest()
    with connect(database) as connection:
        before_schema = connection.execute(
            "select type, name, tbl_name, sql from sqlite_master order by type, name"
        ).fetchall()
        before_rows = {
            table: connection.execute(f'select * from "{table}" order by id').fetchall()
            for table in (MEANING, HISTORY, CITATION)
        }

    result = step(tmp_path, database, "downgrade", REVISION_0010)
    combined = f"{result.stdout}\n{result.stderr}"
    assert result.returncode != 0, "lossy downgrade must be refused"
    for reason in reasons:
        assert reason in combined, combined
    assert sha256(database.read_bytes()).hexdigest() == before_bytes
    with connect(database) as connection:
        assert connection.execute(
            "select type, name, tbl_name, sql from sqlite_master order by type, name"
        ).fetchall() == before_schema
        for table, rows in before_rows.items():
            assert connection.execute(f'select * from "{table}" order by id').fetchall() == rows
    assert alembic_revision(database) == REVISION_0011


def test_downgrade_refuses_play_with_citations_and_reused_group_slots(tmp_path: Path) -> None:
    database = migrated(tmp_path, REVISION_0011)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database, "play")
    first = None
    for pos_key, order, text in (
        ("verb", 1, "玩"), ("verb", 2, "演奏"), ("verb", 3, "播放"), ("noun", 1, "剧"),
    ):
        meaning_id = insert_meaning(
            database, entry_id, order=order, text=text, pos_key=pos_key,
            pos_source="reviewer", pos_evidence="zhwiktionary:play:1",
        )
        if first is None:
            first = meaning_id
    insert_citation(database, first, locator="wikdict:play:1")

    assert_refused_without_changes(
        tmp_path, database, "附加引用", "词性", "位次冲突",
    )


def test_downgrade_refuses_citation_even_when_meaning_uses_0010_defaults(
    tmp_path: Path,
) -> None:
    database = migrated(tmp_path, REVISION_0011)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)
    meaning_id = insert_meaning(database, entry_id)
    insert_citation(database, meaning_id)

    assert_refused_without_changes(tmp_path, database, "附加引用")


def test_downgrade_refuses_pos_information_even_without_slot_collision(tmp_path: Path) -> None:
    database = migrated(tmp_path, REVISION_0011)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)
    insert_meaning(
        database, entry_id, pos_key="verb", pos_source="reviewer",
        pos_evidence="zhwiktionary:prior:1",
    )

    assert_refused_without_changes(tmp_path, database, "词性")


def test_downgrade_names_a_nondefault_language_as_a_blocker(tmp_path: Path) -> None:
    database = migrated(tmp_path, REVISION_0011)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)
    insert_meaning(database, entry_id, language="en")

    assert_refused_without_changes(tmp_path, database, "language='en'")


def test_downgrade_refuses_pos_information_kept_only_in_history(tmp_path: Path) -> None:
    database = migrated(tmp_path, REVISION_0011)
    connection = connect(database)
    try:
        connection.execute(
            "insert into entry_concise_meaning_revision "
            "(normalized_word, action, display_order, text, created_at, "
            "pos_key, pos_order, pos_source) "
            "values ('play', 'reject', 1, '玩', ?, 'verb', 1, 'reviewer')",
            (NOW,),
        )
        connection.commit()
    finally:
        connection.close()

    assert_refused_without_changes(tmp_path, database, "历史", "词性")


def test_downgrade_keeps_0010_representable_meaning_and_history(tmp_path: Path) -> None:
    database = migrated(tmp_path, REVISION_0011)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)
    meaning_id = insert_meaning(database, entry_id, text="先前的")
    connection = connect(database)
    try:
        connection.execute(
            "insert into entry_concise_meaning_revision "
            "(lexicon_entry_id, normalized_word, concise_meaning_id, action, "
            "display_order, text, created_at) values (?, 'prior', ?, 'propose', 1, ?, ?)",
            (entry_id, meaning_id, "先前的", NOW),
        )
        connection.commit()
    finally:
        connection.close()

    result = step(tmp_path, database, "downgrade", REVISION_0010)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert alembic_revision(database) == REVISION_0010
    assert read_meaning(database, entry_id)[0] == "先前的"
    connection = connect(database)
    try:
        assert connection.execute(f"select text from {HISTORY}").fetchone()[0] == "先前的"
    finally:
        connection.close()


def read_meaning(database: Path, entry_id: int) -> tuple:
    connection = connect(database)
    try:
        row = connection.execute(
            "select text, provenance_kind, source_locator, derivation_note, status "
            "from entry_concise_meaning where lexicon_entry_id = ?",
            (entry_id,),
        ).fetchone()
        return tuple(row)
    finally:
        connection.close()


# --- the slot: one live row per group and position ----------------------------


def test_one_live_row_per_group_and_position(tmp_path: Path) -> None:
    """``display_order`` is now a position *within a group*, and the group is part of
    the key. Both halves matter: the same position in two groups must coexist, and the
    same position in one group must not."""
    database = migrated(tmp_path)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)

    insert_meaning(database, entry_id, pos_key="adj", pos_order=1, pos_source="pos_section",
                   pos_evidence="zhwiktionary:9576029:12")
    insert_meaning(database, entry_id, order=2, pos_key="adj", pos_order=1,
                   pos_source="pos_section", pos_evidence="zhwiktionary:9576029:12")
    # The same position in a *second* group: the whole point of the change.
    insert_meaning(database, entry_id, order=1, pos_key="adv", pos_order=2,
                   pos_source="pos_section", pos_evidence="zhwiktionary:9576029:19")

    message = refused(
        database,
        lambda: insert_meaning(database, entry_id, order=1, pos_key="adj", pos_order=1,
                               pos_source="pos_section",
                               pos_evidence="zhwiktionary:9576029:12"),
    )
    assert "unique" in message.lower()


def test_two_unclassified_candidates_share_one_slot(tmp_path: Path) -> None:
    """An undetermined group is a group: it has one slot, not an unlimited supply.

    Otherwise two drafts could sit in the same position and the second would be
    silently invisible rather than conflicting.
    """
    database = migrated(tmp_path)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)
    insert_meaning(database, entry_id, pos_key="", pos_source="none")
    message = refused(
        database, lambda: insert_meaning(database, entry_id, text="另一草稿")
    )
    assert "unique" in message.lower()


def test_a_withdrawn_value_frees_its_slot(tmp_path: Path) -> None:
    """Withdrawing is how a displayed value changes, so it must free the position.

    The unique index is partial on ``status <> 'rejected'`` for exactly this reason; a
    rebuild that recreated it as a whole-table index would make a withdrawal permanent.
    """
    database = migrated(tmp_path)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)
    insert_meaning(database, entry_id, status="rejected", pos_key="adj", pos_order=1,
                   pos_source="reviewer", pos_evidence="zhwiktionary:9576029:15")
    insert_meaning(database, entry_id, text="新值", pos_key="adj", pos_order=1,
                   pos_source="reviewer", pos_evidence="zhwiktionary:9576029:15")


def test_the_cap_is_three_per_group_and_there_is_no_cap_on_groups(tmp_path: Path) -> None:
    """The product rule is per group; the number of groups is a fact about the word.

    ``play`` is the case that forced this: three verb senses and one noun sense is four
    values, which the old three-slot shape could not hold. A cap on groups would either
    reject a real word or drop one of its parts of speech, so there is none -- and this
    test writes four groups of three to say so.
    """
    database = migrated(tmp_path)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)

    groups = ("noun", "verb", "adj", "adv")
    for group_order, pos_key in enumerate(groups, start=1):
        for slot in (1, 2, 3):
            insert_meaning(
                database, entry_id, order=slot, text=f"{pos_key}-{slot}",
                pos_key=pos_key, pos_order=group_order, pos_source="reviewer",
                pos_evidence="zhwiktionary:7993707:9",
            )
    assert len(rows_of(database, entry_id)) == 12

    message = refused(
        database,
        lambda: insert_meaning(database, entry_id, order=4, text="第四位",
                               pos_key="noun", pos_order=1, pos_source="reviewer",
                               pos_evidence="zhwiktionary:7993707:9"),
    )
    assert "ck_entry_concise_meaning_order_range" in message


def rows_of(database: Path, entry_id: int) -> list[tuple]:
    connection = connect(database)
    try:
        return [
            tuple(row)
            for row in connection.execute(
                "select pos_key, pos_order, display_order, text from entry_concise_meaning "
                "where lexicon_entry_id = ? order by pos_order, display_order",
                (entry_id,),
            )
        ]
    finally:
        connection.close()


def test_group_order_is_positive_and_has_no_upper_bound(tmp_path: Path) -> None:
    """There is deliberately no ceiling on ``pos_order``: a quota there would silently
    drop a real group. Only "the first group is 1" is structural."""
    database = migrated(tmp_path)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)

    message = refused(
        database,
        lambda: insert_meaning(database, entry_id, pos_key="noun", pos_order=0,
                               pos_source="reviewer",
                               pos_evidence="zhwiktionary:9576029:15"),
    )
    assert "ck_entry_concise_meaning_pos_order_positive" in message

    insert_meaning(database, entry_id, pos_key="noun", pos_order=99,
                   pos_source="reviewer", pos_evidence="zhwiktionary:9576029:15")


# --- a part of speech always says where it came from --------------------------


def test_an_undetermined_part_of_speech_is_the_empty_string_not_null(tmp_path: Path) -> None:
    """0010's rule, applied here: "unknown" has exactly one spelling and it is ``''``.

    A NULL would be a second spelling of the same state, and every constraint and query
    would then have to compare the two as equal. The insert below therefore succeeds with
    the defaults and cannot be written with NULL at all.
    """
    database = migrated(tmp_path)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)
    meaning_id = insert_meaning(database, entry_id)

    connection = connect(database)
    try:
        row = connection.execute(
            "select pos_key, pos_source, pos_evidence_locator, pos_order, language "
            "from entry_concise_meaning where id = ?",
            (meaning_id,),
        ).fetchone()
        assert tuple(row) == ("", "none", "", 1, "")
    finally:
        connection.close()

    message = refused(
        database,
        lambda: write_raw(
            database,
            "update entry_concise_meaning set pos_key = NULL where id = ?",
            (meaning_id,),
        ),
    )
    assert "notnull" in message.lower() or "NOT NULL" in message


def test_a_part_of_speech_cannot_be_asserted_without_its_source(tmp_path: Path) -> None:
    """Both halves of the pairing, in both directions.

    ``pos_key`` set with ``pos_source = 'none'`` is a part of speech nobody vouched for;
    ``pos_source`` set with an empty ``pos_key`` is a provenance for nothing. Neither is
    representable, which is what makes "the part of speech came from the source's own
    heading, or a named human judged it" true of the stored data rather than of the code.
    """
    database = migrated(tmp_path)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)

    message = refused(
        database,
        lambda: insert_meaning(database, entry_id, pos_key="noun", pos_source="none"),
    )
    assert "ck_entry_concise_meaning_pos_key_matches_source" in message

    message = refused(
        database,
        lambda: insert_meaning(database, entry_id, pos_key="", pos_source="reviewer",
                               pos_evidence="zhwiktionary:9576029:15"),
    )
    assert "ck_entry_concise_meaning_pos_key_matches_source" in message


def test_an_established_part_of_speech_must_name_its_evidence(tmp_path: Path) -> None:
    """A heading in the pinned revision, or the gloss line a human judged -- either way
    a position, never a bare assertion."""
    database = migrated(tmp_path)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)

    message = refused(
        database,
        lambda: insert_meaning(database, entry_id, pos_key="noun", pos_source="pos_section"),
    )
    assert "ck_entry_concise_meaning_pos_evidence" in message

    # ...and the reverse: an undetermined part of speech may not carry evidence, so it
    # cannot look like something a source had settled.
    message = refused(
        database,
        lambda: insert_meaning(database, entry_id, pos_key="", pos_source="none",
                               pos_evidence="zhwiktionary:9576029:12"),
    )
    assert "ck_entry_concise_meaning_pos_evidence" in message

    insert_meaning(database, entry_id, pos_key="adj", pos_order=1,
                   pos_source="pos_section", pos_evidence="zhwiktionary:9576029:12")
    insert_meaning(database, entry_id, order=2, pos_key="verb", pos_order=2,
                   pos_source="reviewer", pos_evidence="zhwiktionary:8457333:10")


def test_the_part_of_speech_vocabulary_is_closed(tmp_path: Path) -> None:
    """``名詞``/``n.``/``Nouns`` must not become groups of their own.

    With free text they would, and the unique slot index could not stop it: one word
    would end up with four "noun" groups and no rule would be violated.
    """
    database = migrated(tmp_path)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)

    for spelling in ("名詞", "n.", "Nouns", "noun ", "adjective"):
        message = refused(
            database,
            lambda spelling=spelling: insert_meaning(
                database, entry_id, pos_key=spelling, pos_source="reviewer",
                pos_evidence="zhwiktionary:9576029:15",
            ),
        )
        assert "ck_entry_concise_meaning_pos_key" in message, spelling


def write_raw(database: Path, statement: str, parameters: tuple = ()) -> None:
    connection = connect(database)
    try:
        connection.execute(statement, parameters)
        connection.commit()
    finally:
        connection.close()


# --- language: a source page carries several languages ------------------------


def test_a_value_cannot_declare_itself_to_be_another_language(tmp_path: Path) -> None:
    """The cross-language leak the trial record found, closed at the schema.

    ``mutter``'s page has Danish, Norwegian and Swedish noun senses beside its English
    ones, and ``fertiliser``'s only Chinese gloss sits in a French verb section. With a
    part-of-speech group but no language, such a gloss would be grouped as an English
    sense. A part of speech is not enough on its own, so a row that declares some other
    language is not storable.

    This closes the *declared* leak, not every one: "not recorded" remains storable, and
    the extractor's own ``cross_language`` rejection is what closes the rest.
    """
    database = migrated(tmp_path)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)

    for other in ("da", "fr", "sv", "zh", "en-US"):
        message = refused(
            database,
            lambda other=other: insert_meaning(database, entry_id, language=other),
        )
        assert "ck_entry_concise_meaning_language" in message, other

    insert_meaning(database, entry_id, language="en", pos_key="noun", pos_order=1,
                   pos_source="reviewer", pos_evidence="zhwiktionary:9576029:15")
    insert_meaning(database, entry_id, order=2, language="", pos_key="verb", pos_order=2,
                   pos_source="reviewer", pos_evidence="zhwiktionary:9576029:16")


# --- citations: a value may rest on more than one source position -------------


def test_a_value_can_rest_on_two_sources(tmp_path: Path) -> None:
    """The ``decrease`` shape: one display value, two positions, two different sources.

    This is why a child table exists rather than a delimited string -- each citation
    carries its own ``source_evidence_id``, so the value stays checkable against both
    sources without either file still being on disk.
    """
    database = migrated(tmp_path)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)
    zh = seed_evidence(database, entry_id, locator=9, text="降低", tag="zh")
    wikdict = seed_evidence(database, entry_id, locator=73, text="减少", tag="wd")

    meaning_id = insert_meaning(
        database, entry_id, text="减少；降低", evidence_id=zh,
        locator="zhwiktionary:8456972:9",
    )
    insert_citation(database, meaning_id, order=1, locator="wikdict:73", evidence_id=wikdict)

    connection = connect(database)
    try:
        rows = connection.execute(
            "select citation_order, citation_locator, source_evidence_id "
            "from entry_concise_meaning_citation where concise_meaning_id = ? "
            "order by citation_order",
            (meaning_id,),
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            (1, "wikdict:73", wikdict)
        ]
        assert rows[0][2] != zh, "the two citations point at different evidence rows"
    finally:
        connection.close()


def test_two_values_may_share_one_source_position(tmp_path: Path) -> None:
    """The ``performance`` shape: ``表演`` and ``执行`` both come from line 10.

    So a citation is not a unique key on the position -- only on the (value, order)
    pair. A design that made the position unique would silently refuse the second sense
    extracted from one line, which is a normal thing for a dictionary line to contain.
    """
    database = migrated(tmp_path)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)
    insert_meaning(database, entry_id, order=1, text="表演", pos_key="noun", pos_order=1,
                   pos_source="reviewer", pos_evidence="zhwiktionary:8457333:10")
    insert_meaning(database, entry_id, order=2, text="执行", pos_key="noun", pos_order=1,
                   pos_source="reviewer", pos_evidence="zhwiktionary:8457333:10")


def test_a_citation_needs_a_position_and_a_unique_order(tmp_path: Path) -> None:
    database = migrated(tmp_path, REVISION_0011)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)
    meaning_id = insert_meaning(database, entry_id)

    message = refused(
        database,
        lambda: insert_citation(database, meaning_id, order=1, locator="   "),
    )
    assert "ck_entry_concise_meaning_citation_locator_present" in message

    message = refused(
        database,
        lambda: insert_citation(database, meaning_id, order=1, locator=" wikdict:37 "),
    )
    assert "ck_entry_concise_meaning_citation_locator_trimmed" in message

    message = refused(database, lambda: insert_citation(database, meaning_id, order=0))
    assert "ck_entry_concise_meaning_citation_order_positive" in message

    insert_citation(database, meaning_id, order=1, locator="wikdict:37")
    message = refused(
        database, lambda: insert_citation(database, meaning_id, order=1, locator="wikdict:38")
    )
    assert "unique" in message.lower()


def test_citations_cascade_with_their_value_and_outlive_their_evidence(tmp_path: Path) -> None:
    """Deleting a value deletes what describes it; losing an evidence row does not.

    Keeping the citation readable after its evidence row is gone is the same rule the
    primary locator follows: the human-readable position survives, so a reader can still
    find the source, and the link degrades instead of the record disappearing.
    """
    database = migrated(tmp_path)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)
    evidence_id = seed_evidence(database, entry_id, locator=37, text="表演", tag="wd")
    meaning_id = insert_meaning(database, entry_id)
    insert_citation(database, meaning_id, order=1, locator="wikdict:37", evidence_id=evidence_id)

    write_raw(database, "delete from entry_source_evidence where id = ?", (evidence_id,))
    connection = connect(database)
    try:
        row = connection.execute(
            "select citation_locator, source_evidence_id from entry_concise_meaning_citation "
            "where concise_meaning_id = ?",
            (meaning_id,),
        ).fetchone()
        assert tuple(row) == ("wikdict:37", None), (
            "the position survives its evidence row; only the join column is cleared"
        )
    finally:
        connection.close()

    write_raw(database, "delete from entry_concise_meaning where id = ?", (meaning_id,))
    connection = connect(database)
    try:
        remaining = connection.execute(
            "select count(*) from entry_concise_meaning_citation where concise_meaning_id = ?",
            (meaning_id,),
        ).fetchone()[0]
        assert remaining == 0, "a citation describes its value and goes with it"
    finally:
        connection.close()


# --- nothing is guessed for rows that already exist ---------------------------


def test_the_upgrade_refuses_rather_than_guess_for_a_confirmed_value(tmp_path: Path) -> None:
    """The rule for pre-existing rows: fail, and say why.

    Defaulting these rows to "undetermined" would let the migration succeed, but it
    would leave a value that is *on the study page* with no part of speech -- the state
    this design exists to prevent. Inferring one is worse: the delivered source maps no
    part-of-speech column, so the value would be a machine guess in the shape of a fact.
    Nothing is written, and the database stays where it was.
    """
    database = migrated(tmp_path, REVISION_0010)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)
    insert_meaning(database, entry_id, status="confirmed", text="已确认值",
                   kind="derived", note="繁转简", confirmed=True)

    result = step(tmp_path, database, "upgrade", REVISION_0011)

    assert result.returncode != 0, "the upgrade must not silently accept these rows"
    combined = f"{result.stdout}\n{result.stderr}"
    assert "已确认" in combined
    assert "已确认值" in combined, "the message names the rows it refused"
    assert "concise-meaning reject" in combined, "and says what to do about it"

    assert alembic_revision(database) == REVISION_0010, (
        "a refusal must leave the schema untouched, not half-migrated"
    )
    assert "pos_key" not in columns(database)
    assert read_meaning(database, entry_id) == ("已确认值", "derived", "zhwiktionary:9576029:15",
                                                "繁转简", "confirmed")


def test_the_upgrade_proceeds_when_nothing_is_confirmed(tmp_path: Path) -> None:
    """Candidates and withdrawn rows default to "undetermined", which is the truth.

    Neither is on a page, so recording that no part of speech has been established is a
    description of them rather than a guess -- and a ``rejected`` row is historical by
    definition.
    """
    database = migrated(tmp_path, REVISION_0010)
    _lexicon_id, entry_id = seed_lexicon_and_entry(database)
    insert_meaning(database, entry_id, status="candidate", text="候选值", order=1,
                   kind="source", note="")
    insert_meaning(database, entry_id, status="rejected", text="被撤回", order=2,
                   kind="source", note="")

    result = step(tmp_path, database, "upgrade", REVISION_0011)

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert alembic_revision(database) == REVISION_0011

    connection = connect(database)
    try:
        rows = connection.execute(
            "select text, status, pos_key, pos_source, pos_evidence_locator, language "
            "from entry_concise_meaning order by display_order"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("候选值", "candidate", "", "none", "", ""),
            ("被撤回", "rejected", "", "none", "", ""),
        ], "existing values are preserved and no part of speech is invented"
        asked = connection.execute(
            "select count(*) from entry_concise_meaning where status = 'confirmed'"
        ).fetchone()[0]
        assert asked == 0, "the migration must not promote anything while migrating"
    finally:
        connection.close()
