"""Migration 0012: the pinned wikitext line a citation will point at.

``entry_source_evidence`` records the physical line of a *converted* file. For
``prior`` that is line 139 of ``zhwiktionary-v4en.csv``, one aggregated cell. The
positions the trial record actually cites are lines of the **wikitext of the page
pinned at an oldid** -- ``9576029:12`` (the ``===形容詞===`` heading), ``:15``, ``:16``,
``:23`` -- and until 0012 nothing could store one, look one up, or tell it apart from
the CSV row number. Migration 0012 adds ``source_wikitext_line``: one row per line of
one page revision, with the page's fingerprint, the paths that govern the line, and --
when the line is itself a part-of-speech heading -- the heading's text and key.

Four things shape these tests:

* **The database is the enforcer.** Every rule is asserted by attempting a write that
  must be refused, and the refusal must name the constraint. A rule only the service
  honours is a rule a later code path can skip.
* **The two "line"s must stay apart.** A CSV row and a wikitext line are compared here
  side by side: their keys differ, the CSV row keeps its own position, and a value that
  is not an oldid cannot be stored as a page revision.
* **A model and a migration that disagree only show up in production.** The table built
  by ``create_all()`` from the ORM and the table built by the migration are compared
  column by column, index by index and constraint by constraint -- including the text
  of each CHECK, because the same rule spelled two ways is exactly how the two shapes
  drift apart.
* **Nothing real is touched.** Every page below is invented for this module, every
  database is a throwaway file under ``tmp_path``, and no row is read from the preserved
  archives in ``test-artifacts/``: this slice stores structure, it does not import a
  lexicon.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from tests.conftest import BACKEND_ROOT, run_alembic

REVISION_0011 = "0011_entry_concise_meaning_pos"
REVISION_0012 = "0012_source_wikitext_line"

TABLE = "source_wikitext_line"
ARTIFACT_TABLE = "source_artifact"
CSV_EVIDENCE = "entry_source_evidence"
POSITION_INDEX = "uq_source_wikitext_line_position"
ARTIFACT_INDEX = "ix_source_wikitext_line_source_artifact_id"

#: The triggers 0012 creates. Spelled out here rather than imported from the migration:
#: a test that reads the list it is checking cannot notice that the list changed.
EXPECTED_TRIGGERS = frozenset(
    {
        "trg_source_wikitext_line_no_update",
        "trg_source_wikitext_line_no_delete",
        "trg_source_wikitext_line_one_page_fingerprint",
    }
)

#: The declared source id a frozen manifest uses for zh.wiktionary pinned pages. The
#: stored column carries the declared id, never the short ``zhwiktionary`` an alias
#: maps from -- resolving that alias is the confirmation entry's job.
SOURCE_ID = "zhwiktionary-pinned-oldid"
#: A revision number that is obviously synthetic: no real page has it.
SYNTHETIC_OLDID = "9000001"
OTHER_OLDID = "9000002"

NOW = "2026-01-01 00:00:00"

#: A synthetic page. It is not a real entry and its glosses are invented; what it
#: reproduces is the *shape* the design is about -- an English section with a heading
#: that is not a part of speech, two part-of-speech headings under it, a second language
#: section whose heading has the same text as an English one, and a leading space that
#: is content rather than noise.
SYNTHETIC_PAGE_LINES: tuple[str, ...] = (
    "==英語==",
    "===發音===",
    "{{合成模板|示例}}",
    "===形容詞===",
    "合成释义甲；合成释义乙",
    " 缩进的合成释义",
    "===動詞===",
    "合成动作释义",
    "==法語==",
    "===動詞===",
    "另一语言段落的合成释义",
)
SYNTHETIC_PAGE = "\n".join(SYNTHETIC_PAGE_LINES)

#: The synthetic "preserved file" that page lives in, shaped like the real one (a JSON
#: object keyed by ``oldid``). The distinction matters and is asserted below: the
#: artifact's own fingerprint binds the *file*, and ``page_text_sha256`` binds the one
#: *page* the lines were read from. A real archive holds many pages, so the two are not
#: the same digest and neither can stand in for the other.
SYNTHETIC_ARCHIVE = json.dumps(
    {"pages": {SYNTHETIC_OLDID: SYNTHETIC_PAGE}}, ensure_ascii=False, sort_keys=True
)

#: A synthetic *converted* file. The other kind of position counts physical lines of a
#: file like this one, header included -- which is why the same integer can be a CSV
#: ``row_locator`` and a wikitext ``line_number`` at the same time, and why the two are
#: stored in different tables under different keys.
SYNTHETIC_CSV = "word,zh_meaning\nsynthetic,合成释义汇总格；另一个义项\n"

#: The heading texts that *are* parts of speech, and the keys they stand for. Test-local
#: on purpose: this slice stores the basis, and the closed set a confirmation resolves
#: headings against is a later slice's rule.
POS_HEADING_KEYS = {"名詞": "noun", "動詞": "verb", "形容詞": "adj", "副詞": "adv"}

HEADING = re.compile(r"^(={2,6})\s*(.*?)\s*\1$")


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def expected_line_sha256(
    *,
    source_id: str,
    page_revision: str,
    page_text_sha256: str,
    line_number: int,
    raw_text: str,
) -> str:
    """The formula ``SourceWikitextLine.line_sha256`` documents, spelled a second time.

    Deliberately *not* imported from ``app.models``: a test that calls the same helper it
    is checking only proves the helper is deterministic. Written out here, a change to
    the formula on either side shows up as a failure instead of moving both.
    """
    return hashlib.sha256(
        "\n".join(
            (source_id, page_revision, page_text_sha256, str(line_number), raw_text)
        ).encode("utf-8")
    ).hexdigest()


# --- a synthetic page, derived the way a reader would -------------------------


def line_facts(lines: Sequence[str]) -> list[tuple[str, str, str, str]]:
    """``(language_path, heading_path, pos_heading_key, pos_heading_text)`` per line.

    A test-local derivation of what the future importer computes, written out plainly so
    the rows below are readable rather than produced by a black box. The rules it
    follows are the ones the columns are defined by:

    * ``heading_path`` is the ancestry that *governs* the line, joined with ``' > '``;
    * a line that is itself a heading is described by ``pos_heading_*`` rather than by
      its own title, so its own heading is not part of its governing ancestry;
    * the language is the level-2 heading in force, and ``''`` when there is none --
      which is the honest answer for the language heading line itself.
    """
    stack: list[tuple[int, str]] = []
    facts: list[tuple[str, str, str, str]] = []
    for line in lines:
        match = HEADING.match(line)
        if match:
            level, title = len(match.group(1)), match.group(2)
            stack = [item for item in stack if item[0] < level]
        language = next((title for level, title in stack if level == 2), "")
        heading = " > ".join(title for _level, title in stack)
        if match:
            key = POS_HEADING_KEYS.get(title, "")
            facts.append((language, heading, key, title if key else ""))
            stack.append((level, title))
        else:
            facts.append((language, heading, "", ""))
    return facts


def synthetic_rows(
    *, source_id: str = SOURCE_ID, oldid: str = SYNTHETIC_OLDID
) -> list[dict]:
    """Every line of the synthetic page as a storable row, with real digests."""
    lines = list(SYNTHETIC_PAGE_LINES)
    page_digest = fingerprint(SYNTHETIC_PAGE)
    rows = []
    for number, (text, facts) in enumerate(zip(lines, line_facts(lines), strict=True), 1):
        language, heading, key, heading_text = facts
        rows.append(
            with_fingerprint(
                {
                    "source_id": source_id,
                    "page_revision": oldid,
                    "line_number": number,
                    "raw_text": text,
                    "language_path": language,
                    "heading_path": heading,
                    "pos_heading_key": key,
                    "pos_heading_text": heading_text,
                    "page_text_sha256": page_digest,
                }
            )
        )
    return rows


def with_fingerprint(fields: dict) -> dict:
    """The same fields with ``line_sha256`` recomputed from them.

    An import would compute the digest from what it is about to store, so the refusal
    cases below can move a field without having to hand-write a new digest -- and a case
    that *wants* a bad digest passes ``line_sha256`` explicitly.
    """
    return {
        **fields,
        "line_sha256": expected_line_sha256(
            source_id=fields["source_id"],
            page_revision=fields["page_revision"],
            page_text_sha256=fields["page_text_sha256"],
            line_number=fields["line_number"],
            raw_text=fields["raw_text"],
        ),
    }


def row_for(line_number: int) -> dict:
    """One synthetic line's row, by its line number."""
    by_number = {row["line_number"]: row for row in synthetic_rows()}
    return by_number[line_number]


#: The lines the tests reason about by name, so an assertion never says "line 4".
ENGLISH_HEADING = 1
PRONUNCIATION_HEADING = 2
ADJECTIVE_HEADING = 4
GLOSS_UNDER_ADJECTIVE = 5
INDENTED_GLOSS = 6
FRENCH_VERB_HEADING = 10
FRENCH_GLOSS = 11


# --- building and reading synthetic databases ---------------------------------


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


def model_database(tmp_path: Path) -> Path:
    """A database whose schema comes from the ORM instead of the migrations.

    ``create_all()`` is never how the application builds a schema; it is used here only
    so the model's own table can be compared with the one the migration creates.
    """
    import app.models  # noqa: F401  (populates Base.metadata)
    from app.db import Base, make_engine
    from app.testing_guards import assert_safe_for_destructive_operation

    database = tmp_path / "model" / "create-all.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    assert_safe_for_destructive_operation(database, action="build a model-only test schema in")
    engine = make_engine(f"sqlite:///{database.as_posix()}")
    Base.metadata.create_all(engine)
    engine.dispose()
    return database


def connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(database))
    connection.row_factory = sqlite3.Row
    connection.execute("pragma foreign_keys=ON")
    return connection


def columns(database: Path, table: str = TABLE) -> list[str]:
    connection = connect(database)
    try:
        return [row["name"] for row in connection.execute(f'pragma table_info("{table}")')]
    finally:
        connection.close()


def column_details(database: Path, table: str = TABLE) -> dict[str, tuple]:
    connection = connect(database)
    try:
        return {
            row["name"]: (row["type"], row["notnull"], row["dflt_value"])
            for row in connection.execute(f'pragma table_info("{table}")')
        }
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


def index_names(database: Path, table: str = TABLE) -> set[str]:
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


def index_columns(database: Path, index: str) -> tuple[str, ...]:
    connection = connect(database)
    try:
        return tuple(
            row["name"] for row in connection.execute(f'pragma index_info("{index}")')
        )
    finally:
        connection.close()


def unique_keys(database: Path, table: str) -> set[tuple[tuple[str, ...], bool]]:
    """Every unique key of one table, whichever way it was declared.

    Covers both spellings this schema uses: a table-level ``UNIQUE`` constraint, which
    SQLite implements as a ``sqlite_autoindex``, and an explicitly named unique index.
    """
    connection = connect(database)
    try:
        found = set()
        for row in connection.execute(f'pragma index_list("{table}")'):
            name, is_unique = row[1], bool(row[2])
            if not is_unique:
                continue
            found.add(
                (
                    tuple(
                        item[2]
                        for item in connection.execute(f'pragma index_info("{name}")')
                    ),
                    True,
                )
            )
        return found
    finally:
        connection.close()


def check_constraints(database: Path, table: str = TABLE) -> dict[str, str]:
    """Named CHECK constraints with their text, via SQLAlchemy's own reflection."""
    engine = sa.create_engine(f"sqlite:///{Path(database).as_posix()}")
    try:
        return {
            item["name"]: " ".join(str(item["sqltext"]).split())
            for item in sa.inspect(engine).get_check_constraints(table)
        }
    finally:
        engine.dispose()


def trigger_names(database: Path, table: str = TABLE) -> set[str]:
    connection = connect(database)
    try:
        return {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='trigger' and tbl_name=?",
                (table,),
            )
        }
    finally:
        connection.close()


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


def seed_artifact(
    database: Path,
    *,
    name: str = "synthetic-pinned-wikitext.json",
    file_text: str = SYNTHETIC_ARCHIVE,
) -> int:
    """One preserved-file row. Its ``file_sha256`` is the digest of the file it names.

    ``source_artifact`` carries its own provenance CHECK -- publisher, version, obtain
    time, licence id and both scopes -- because an artifact nobody could audit is not an
    artifact; all of them are filled here with synthetic values.
    """
    connection = connect(database)
    try:
        connection.execute(
            "insert into source_artifact (role, name, publisher, version, "
            "obtained_at_utc, format, mapping_json, mapping_sha256, file_sha256, "
            "byte_size, license_id, license_text_sha256, use_scope, display_scope, "
            "storage_locator, created_at) values ('meaning', ?, '合成来源', 'pinned', "
            "'2026-01-01T00:00:00Z', 'json', '{}', ?, ?, ?, 'SYNTHETIC', ?, "
            "'合成测试范围', '合成测试范围', 'nowhere', ?)",
            (
                name,
                fingerprint("{}"),
                fingerprint(file_text),
                len(file_text.encode("utf-8")),
                fingerprint("synthetic-licence"),
                NOW,
            ),
        )
        artifact_id = connection.execute("select last_insert_rowid()").fetchone()[0]
        connection.commit()
        return artifact_id
    finally:
        connection.close()


def insert_line(database: Path, artifact_id: int, row: dict, **overrides) -> int:
    """Insert one line row. Raises whatever SQLite raises, on purpose."""
    fields = {"source_artifact_id": artifact_id, **row, **overrides}
    if "line_sha256" not in overrides:
        fields = with_fingerprint(fields)
    fields.setdefault("created_at", NOW)
    names = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    connection = connect(database)
    try:
        connection.execute(
            f"insert into {TABLE} ({names}) values ({placeholders})",
            tuple(fields.values()),
        )
        line_id = connection.execute("select last_insert_rowid()").fetchone()[0]
        connection.commit()
        return line_id
    finally:
        connection.close()


def insert_page(database: Path, artifact_id: int) -> list[int]:
    """Every line of the synthetic page, in order."""
    return [insert_line(database, artifact_id, row) for row in synthetic_rows()]


def seed_csv_evidence(database: Path, *, row_locator: int = 139) -> int:
    """One *converted CSV* evidence row, with the chain of parents it needs.

    This is the other kind of position, and the point of seeding it here is that the two
    can be compared in one database: it counts physical lines of the converted file,
    carries no page revision, and keeps its own unique key.

    The artifact is written through its own helper first: one connection at a time, or
    the second one would wait on a write lock the first is still holding.
    """
    artifact_id = seed_artifact(
        database, name="synthetic-converted.csv", file_text=SYNTHETIC_CSV
    )
    connection = connect(database)
    try:
        connection.execute(
            "insert into lexicon (owner_user_id, name, description, visibility, "
            "source_type, entry_count, created_at, updated_at) values (null, 'synthetic', "
            "'', 'public', 'synthetic', 1, ?, ?)",
            (NOW, NOW),
        )
        lexicon_id = connection.execute("select last_insert_rowid()").fetchone()[0]
        connection.execute(
            "insert into public_import_run (plan_sha256, run_id, target_lexicon_id, "
            "confirmed_by_username, confirmed_at, status, entries_created, "
            "entries_matched, evidence_written, result_json, error_report_locator, "
            "created_at) values (?, 'synthetic-run', ?, 'owner', ?, 'applied', 0, 0, 1, "
            "'{}', '', ?)",
            (fingerprint("plan"), lexicon_id, NOW, NOW),
        )
        run_id = connection.execute("select last_insert_rowid()").fetchone()[0]
        connection.execute(
            "insert into entry_source_evidence (lexicon_entry_id, source_artifact_id, "
            "import_run_id, normalized_word, row_locator, field_kind, sense_key, raw_word, "
            "raw_text, evidence_sha256, decision, selected_for_default, "
            "confirmed_by_username, confirmed_at, source_revision) values "
            "(null, ?, ?, 'synthetic', ?, 'meaning', ?, 'synthetic', "
            "'合成释义汇总格；另一个义项', ?, 'selected', 1, 'owner', ?, '')",
            (
                artifact_id,
                run_id,
                row_locator,
                f"meaning@{row_locator}",
                fingerprint("synthetic-cell"),
                NOW,
            ),
        )
        evidence_id = connection.execute("select last_insert_rowid()").fetchone()[0]
        connection.commit()
        return evidence_id
    finally:
        connection.close()


def seed_dangling_evidence(database: Path, *, row_locator: int = 1) -> None:
    """One evidence row whose parents do not exist, written with enforcement off.

    This is the only way such a row can exist: ``alembic/env.py`` runs migrations with
    foreign-key enforcement *off*, which is exactly why a migration has to ask whether
    the database it is about to change is consistent instead of assuming it.
    """
    connection = sqlite3.connect(str(database))
    try:
        connection.execute("pragma foreign_keys=OFF")
        connection.execute(
            "insert into entry_source_evidence (lexicon_entry_id, source_artifact_id, "
            "import_run_id, normalized_word, row_locator, field_kind, sense_key, raw_word, "
            "raw_text, evidence_sha256, decision, selected_for_default, "
            "confirmed_by_username, confirmed_at, source_revision) values "
            "(null, 999999, 999999, 'dangling', ?, 'meaning', ?, 'dangling', "
            "'合成悬挂行', ?, 'selected', 1, 'owner', ?, '')",
            (row_locator, f"meaning@{row_locator}", fingerprint("dangling"), NOW),
        )
        connection.commit()
    finally:
        connection.close()


def read_evidence(database: Path, evidence_id: int) -> tuple:
    connection = connect(database)
    try:
        row = connection.execute(
            f"select * from {CSV_EVIDENCE} where id = ?", (evidence_id,)
        ).fetchone()
        return tuple(row)
    finally:
        connection.close()


def read_line(database: Path, line_id: int) -> sqlite3.Row:
    connection = connect(database)
    try:
        return connection.execute(f"select * from {TABLE} where id = ?", (line_id,)).fetchone()
    finally:
        connection.close()


def read_artifact(database: Path, artifact_id: int) -> sqlite3.Row:
    connection = connect(database)
    try:
        return connection.execute(
            f"select * from {ARTIFACT_TABLE} where id = ?", (artifact_id,)
        ).fetchone()
    finally:
        connection.close()


def find_line(database: Path, *, source_id: str, oldid: str, line_number: int) -> list:
    """Rows for one locator position, read the way a reader would resolve one."""
    connection = connect(database)
    try:
        return [
            tuple(row)
            for row in connection.execute(
                f"select id, raw_text from {TABLE} where source_id = ? and "
                "page_revision = ? and line_number = ?",
                (source_id, oldid, line_number),
            )
        ]
    finally:
        connection.close()


def write_raw(database: Path, sql: str, parameters: tuple = ()) -> None:
    """Run one statement that is expected to succeed."""
    connection = connect(database)
    try:
        connection.execute(sql, parameters)
        connection.commit()
    finally:
        connection.close()


def refused(database: Path, action) -> str:
    """Run a write that must be refused, and hand back the engine's message."""
    with pytest.raises(sqlite3.IntegrityError) as error:
        action()
    return str(error.value)


# --- the chain and the shared vocabulary --------------------------------------


def test_0012_is_the_single_head_and_stacks_on_0011() -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == [REVISION_0012], (
        "adding 0012 must keep exactly one head; a second head means the graph branched"
    )
    assert script.get_revision(REVISION_0012).down_revision == REVISION_0011, (
        "0012 follows 0011 rather than editing it in place"
    )


def load_migration():
    """The 0012 module itself, loaded by path.

    ``alembic/versions`` is not an importable package -- Alembic finds revisions through
    its own script directory -- so the file is loaded directly rather than imported.
    """
    import importlib.util

    path = BACKEND_ROOT / "alembic" / "versions" / "0012_source_wikitext_line.py"
    spec = importlib.util.spec_from_file_location("migration_0012", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_vocabulary_matches_the_models() -> None:
    """A value legal in Python and illegal in the database is a production-only bug.

    The migration spells its vocabulary out as literals -- a revision has to describe
    the shape it created even after the models move on -- so the two are compared here.
    The part-of-speech keys are the same closed set migration 0011 gave the display
    grouping: a heading that establishes a part of speech no group can hold would be a
    fact stored where nothing could show it.
    """
    migration = load_migration()
    from app import models

    assert migration.POS_KEYS == models.CONCISE_MEANING_POS_KEYS
    assert migration.POS_KEY_SQL == models.SOURCE_WIKITEXT_LINE_POS_KEY_SQL
    assert migration.PATH_SEPARATOR == models.SOURCE_WIKITEXT_LINE_PATH_SEPARATOR


# --- the table the migration builds -------------------------------------------


def test_0012_appends_one_table_and_touches_nothing_else(tmp_path: Path) -> None:
    """0011 -> 0012 on a database that already holds a converted-CSV evidence row.

    Nothing existing is rebuilt or altered, so the assertion that matters is that the
    row written before the upgrade reads back byte for byte afterwards: its position is
    a physical line of the converted file, it has no page revision, and the new table
    does not gain a line for it.
    """
    database = migrated(tmp_path, REVISION_0011)
    evidence_id = seed_csv_evidence(database, row_locator=139)

    before_tables = table_names(database)
    before_count = foreign_key_count(database)
    before_row = read_evidence(database, evidence_id)
    assert TABLE not in before_tables

    step(tmp_path, database, "upgrade", REVISION_0012)

    assert table_names(database) - before_tables == {TABLE}, (
        "0012 adds exactly one table and alters no other"
    )
    assert foreign_key_count(database) == before_count + 1, (
        "the new table carries exactly one foreign key, to the preserved artifact"
    )
    assert read_evidence(database, evidence_id) == before_row, (
        "the CSV position keeps its row_locator and its empty page revision"
    )
    connection = connect(database)
    try:
        assert connection.execute(f"select count(*) from {TABLE}").fetchone()[0] == 0, (
            "upgrading must not invent a wikitext line for a CSV position"
        )
    finally:
        connection.close()
    assert alembic_revision(database) == REVISION_0012
    assert integrity(database) == ("ok", [])


def test_the_new_table_has_the_shape_its_columns_claim(tmp_path: Path) -> None:
    database = migrated(tmp_path)

    assert columns(database) == [
        "id",
        "source_artifact_id",
        "source_id",
        "page_revision",
        "line_number",
        "raw_text",
        "language_path",
        "heading_path",
        "pos_heading_key",
        "pos_heading_text",
        "page_text_sha256",
        "line_sha256",
        "created_at",
    ]

    details = column_details(database)
    for name, expected_default in (
        ("language_path", "''"),
        ("heading_path", "''"),
        ("pos_heading_key", "''"),
        ("pos_heading_text", "''"),
    ):
        _type, not_null, default = details[name]
        assert not_null == 1, f"{name} must be NOT NULL: 'unknown' is not a NULL here"
        assert default == expected_default, f"{name} default is {default!r}"

    for name in ("raw_text", "source_id", "page_revision", "page_text_sha256", "line_sha256"):
        _type, not_null, default = details[name]
        assert not_null == 1, f"{name} is a fact the row has to state"
        assert default is None, (
            f"{name} must have no server default: a writer has to name the value, and a "
            "default would let a row exist without one"
        )

    assert foreign_keys(database) == {
        "source_artifact_id": (ARTIFACT_TABLE, "RESTRICT")
    }
    assert index_names(database) == {POSITION_INDEX, ARTIFACT_INDEX}
    assert index_columns(database, POSITION_INDEX) == (
        "source_id",
        "page_revision",
        "line_number",
    )
    assert index_columns(database, ARTIFACT_INDEX) == ("source_artifact_id",)
    assert trigger_names(database) == EXPECTED_TRIGGERS, (
        "the append-only and one-page-fingerprint rules exist only as triggers; "
        "a table without them reads exactly like a table with them"
    )
    assert alembic_revision(database) == REVISION_0012


def test_the_model_builds_the_same_table_as_the_migration(tmp_path: Path) -> None:
    """The ORM and the revision must describe one table, not two similar ones.

    Column order, nullability, the foreign key, the indexes and -- above all -- the text
    of every CHECK are compared. Comparing the conditions rather than only their names
    is deliberate: a model whose rule says ``>= 1`` while the migration says ``>= 0``
    would pass a name-only check and then hold a database that enforces the weaker one.

    The triggers are the one part that deliberately does **not** match, and the
    difference is asserted rather than left implicit: SQLAlchemy has no construct for
    them, so the append-only and one-page-fingerprint rules live only in the schema the
    migrations build. ``create_all`` is never how the application builds a schema -- it
    appears here so the model's own table can be compared -- but a reader should not have
    to discover that by trying to update a row on the wrong database.
    """
    from app.db import Base

    built = model_database(tmp_path)
    migrated_database = migrated(tmp_path)
    model_table = Base.metadata.tables[TABLE]

    assert columns(built) == columns(migrated_database)
    assert column_details(built) == column_details(migrated_database)
    assert foreign_keys(built) == foreign_keys(migrated_database)
    assert index_names(built) == index_names(migrated_database)
    for index in (POSITION_INDEX, ARTIFACT_INDEX):
        assert index_columns(built, index) == index_columns(migrated_database, index)

    assert check_constraints(built) == check_constraints(migrated_database), (
        "the model and the migration must spell each rule the same way"
    )
    assert set(check_constraints(migrated_database)) == {
        constraint.name for constraint in model_table.constraints if constraint.name
    }
    assert trigger_names(migrated_database) == EXPECTED_TRIGGERS
    assert trigger_names(built) == set(), (
        "SQLAlchemy cannot express a trigger, so the model's own table has none -- which "
        "is why the migrations are the schema users get"
    )


# --- one line, several lines, the same position -------------------------------


def test_several_lines_of_one_page_are_stored_side_by_side(tmp_path: Path) -> None:
    """A page has many cited lines, and each is its own row with its own paths.

    The point of the position key is that it is per *line*: a page is not recorded once,
    and two lines of the same revision are not the same fact. The rows also show what
    makes a part-of-speech basis usable -- the ``===發音===`` heading is a heading, and
    yet it can never establish a part of speech, because it has no key.
    """
    database = migrated(tmp_path)
    artifact_id = seed_artifact(database)
    insert_page(database, artifact_id)
    rows = synthetic_rows()

    connection = connect(database)
    try:
        stored = connection.execute(
            f"select line_number, raw_text, language_path, heading_path, pos_heading_key, "
            f"pos_heading_text from {TABLE} where page_revision = ? order by line_number",
            (SYNTHETIC_OLDID,),
        ).fetchall()
    finally:
        connection.close()

    assert len(stored) == len(rows) == len(SYNTHETIC_PAGE_LINES), (
        "every line of the page is its own row; the page is not one row"
    )
    assert [tuple(row) for row in stored] == [
        (
            row["line_number"],
            row["raw_text"],
            row["language_path"],
            row["heading_path"],
            row["pos_heading_key"],
            row["pos_heading_text"],
        )
        for row in rows
    ]

    by_number = {row["line_number"]: row for row in stored}
    assert by_number[ENGLISH_HEADING]["language_path"] == "", (
        "the language heading itself is governed by no language section"
    )
    assert by_number[PRONUNCIATION_HEADING]["pos_heading_key"] == "", (
        "發音 is a heading but not a part of speech, so it can never be a basis for one"
    )
    assert by_number[PRONUNCIATION_HEADING]["heading_path"] == "英語"
    assert by_number[ADJECTIVE_HEADING]["pos_heading_key"] == "adj"
    assert by_number[ADJECTIVE_HEADING]["pos_heading_text"] == "形容詞"
    assert by_number[ADJECTIVE_HEADING]["heading_path"] == "英語", (
        "a heading line is described by its own key, not by its own title"
    )
    assert by_number[GLOSS_UNDER_ADJECTIVE]["heading_path"] == "英語 > 形容詞"
    assert by_number[GLOSS_UNDER_ADJECTIVE]["pos_heading_key"] == ""
    assert by_number[INDENTED_GLOSS]["raw_text"] == " 缩进的合成释义", (
        "the line is stored byte for byte; indentation is content, not noise"
    )
    assert by_number[FRENCH_VERB_HEADING]["language_path"] == "法語"
    assert by_number[FRENCH_VERB_HEADING]["pos_heading_key"] == "verb"
    assert by_number[FRENCH_GLOSS]["heading_path"] == "法語 > 動詞", (
        "the same heading text in another language is another section: the path, not the "
        "title, is what says which language a line belongs to"
    )


def test_a_position_can_only_be_recorded_once(tmp_path: Path) -> None:
    """One revision of one page has exactly one line 15, so it has one row.

    The other half matters just as much: a different line of the same page, the same
    line number under another revision, and the same revision under another source are
    each a different position and must all be storable.
    """
    database = migrated(tmp_path)
    artifact_id = seed_artifact(database)
    insert_line(database, artifact_id, row_for(GLOSS_UNDER_ADJECTIVE))

    message = refused(
        database,
        lambda: insert_line(database, artifact_id, row_for(GLOSS_UNDER_ADJECTIVE)),
    )
    assert "unique" in message.lower()
    assert "source_id" in message and "line_number" in message

    # The same text at another line number is another position, not a duplicate.
    insert_line(
        database, artifact_id, row_for(GLOSS_UNDER_ADJECTIVE), line_number=16
    )
    # ...and so are another revision and another declared source.
    insert_line(database, artifact_id, row_for(GLOSS_UNDER_ADJECTIVE), page_revision=OTHER_OLDID)
    insert_line(database, artifact_id, row_for(GLOSS_UNDER_ADJECTIVE), source_id="other-source")

    assert len(find_line(
        database,
        source_id=SOURCE_ID,
        oldid=SYNTHETIC_OLDID,
        line_number=GLOSS_UNDER_ADJECTIVE,
    )) == 1, "a locator must resolve to exactly one row"
    assert len(find_line(
        database, source_id=SOURCE_ID, oldid=SYNTHETIC_OLDID, line_number=16
    )) == 1
    assert len(find_line(
        database, source_id="other-source", oldid=SYNTHETIC_OLDID,
        line_number=GLOSS_UNDER_ADJECTIVE,
    )) == 1


def test_a_csv_position_keeps_its_own_row_and_its_own_key(tmp_path: Path) -> None:
    """``139`` and ``15`` are different facts, and the schema says so twice over.

    ``prior``'s converted CSV row is physical line 139 of the delivered file, with no
    page revision; the cited wikitext lines are 12, 15, 16, 19 and 23 of oldid 9576029.
    Here the same integer is stored as both kinds of position -- a CSV ``row_locator``
    and a wikitext ``line_number`` -- and the two are still not confusable: each table
    has its own identity key, and neither row can be read out of the other's.
    """
    database = migrated(tmp_path)
    evidence_id = seed_csv_evidence(database, row_locator=139)
    artifact_id = seed_artifact(database)
    insert_line(database, artifact_id, row_for(GLOSS_UNDER_ADJECTIVE), line_number=139)

    assert unique_keys(database, CSV_EVIDENCE) == {(("evidence_sha256", "import_run_id"), True)}, (
        "a CSV position is identified by its fingerprint inside one import run"
    )
    assert unique_keys(database, TABLE) == {
        (("source_id", "page_revision", "line_number"), True)
    }, "a wikitext line is identified by source, page revision and line"

    evidence = read_evidence(database, evidence_id)
    assert evidence[5] == 139, "the CSV row keeps counting physical lines of its file"
    assert evidence[-1] == "", "and it has no page revision to record"

    stored = read_line(database, insert_line(
        database, artifact_id, row_for(GLOSS_UNDER_ADJECTIVE), line_number=140
    ))
    assert stored["line_number"] == 140
    assert stored["page_revision"] == SYNTHETIC_OLDID
    assert stored["page_text_sha256"] == fingerprint(SYNTHETIC_PAGE)
    artifact = read_artifact(database, artifact_id)
    assert artifact["file_sha256"] == fingerprint(SYNTHETIC_ARCHIVE)
    assert artifact["file_sha256"] != stored["page_text_sha256"], (
        "the artifact's fingerprint binds the preserved file; page_text_sha256 binds the "
        "one page these lines were read from, and an archive holds many pages"
    )

    # A version string that is not an oldid cannot be a page revision, so a CSV row's
    # own version column cannot be stored as though it named a page revision.
    message = refused(
        database,
        lambda: insert_line(
            database, artifact_id, row_for(GLOSS_UNDER_ADJECTIVE), page_revision="70dc6b68"
        ),
    )
    assert "ck_source_wikitext_line_revision_digits" in message


def test_the_stored_fingerprint_is_the_one_the_row_actually_has(tmp_path: Path) -> None:
    """``line_sha256`` binds the page, the position and the text together.

    The digest is recomputed here from the documented formula rather than from the value
    that was inserted, and it is checked to *change* when any part of the identity
    changes -- otherwise it would be a constant that verifies nothing.
    """
    database = migrated(tmp_path)
    artifact_id = seed_artifact(database)
    row = row_for(GLOSS_UNDER_ADJECTIVE)
    assert row["line_sha256"] == expected_line_sha256(
        source_id=row["source_id"],
        page_revision=row["page_revision"],
        page_text_sha256=row["page_text_sha256"],
        line_number=row["line_number"],
        raw_text=row["raw_text"],
    )

    line_id = insert_line(database, artifact_id, row)
    stored = read_line(database, line_id)
    assert stored["line_sha256"] == row["line_sha256"]

    baseline = {
        "source_id": row["source_id"],
        "page_revision": row["page_revision"],
        "page_text_sha256": row["page_text_sha256"],
        "line_number": row["line_number"],
        "raw_text": row["raw_text"],
    }
    for field, changed in (
        ("raw_text", "合成释义甲；合成释义丙"),
        ("page_revision", OTHER_OLDID),
        ("page_text_sha256", fingerprint("another page")),
        ("line_number", 16),
        ("source_id", "other-source"),
    ):
        assert expected_line_sha256(**{**baseline, field: changed}) != row["line_sha256"], (
            f"changing {field} must change the fingerprint, or it proves nothing"
        )

    # The database checks the *shape* of a fingerprint, not its content: only the
    # confirmation step can recompute it. This is stated rather than implied, because a
    # reader who believed the CHECK verified the text would trust a row nobody verified.
    insert_line(database, artifact_id, row_for(FRENCH_GLOSS), line_sha256="0" * 64)


# --- what the database makes impossible in place ------------------------------


def test_a_recorded_line_cannot_be_updated_or_deleted(tmp_path: Path) -> None:
    """Evidence is append-only in the database, not by convention.

    ``entry_concise_meaning_revision`` has the same rule held by a statement hook in the
    service; a recorded line is held by the database itself, because a rewritten row is
    indistinguishable from an honestly recorded one -- it still claims to be the source's
    own line, and only a reader who recomputed ``line_sha256`` would see otherwise. Both
    statements are refused, and the row is untouched afterwards.
    """
    database = migrated(tmp_path)
    artifact_id = seed_artifact(database)
    line_id = insert_line(database, artifact_id, row_for(GLOSS_UNDER_ADJECTIVE))
    before = tuple(read_line(database, line_id))

    message = refused(
        database,
        lambda: write_raw(
            database, f"update {TABLE} set raw_text = '改写' where id = ?", (line_id,)
        ),
    )
    assert "trg_source_wikitext_line_no_update" in message
    assert "只追加" in message

    message = refused(
        database,
        lambda: write_raw(database, f"delete from {TABLE} where id = ?", (line_id,)),
    )
    assert "trg_source_wikitext_line_no_delete" in message

    assert tuple(read_line(database, line_id)) == before, (
        "a refused write must leave the row exactly as it was"
    )


def test_a_line_number_is_a_number_and_not_a_bit_of_text(tmp_path: Path) -> None:
    """SQLite keeps non-numeric text in an INTEGER column, and orders it after every line.

    So the column is checked for what a value *is*, not only for how large it is: a
    stored ``'abc'`` satisfies ``line_number >= 1`` -- SQLite sorts every INTEGER before
    every TEXT -- and would sit where a line number belongs. Text that affinity converts
    to a number is a number, which is the correct answer: what matters is what is stored.
    """
    database = migrated(tmp_path)
    artifact_id = seed_artifact(database)

    for label, value in (("文本", "abc"), ("小数", 15.5)):
        message = refused(
            database,
            lambda value=value: insert_line(
                database, artifact_id, row_for(GLOSS_UNDER_ADJECTIVE), line_number=value
            ),
        )
        assert "ck_source_wikitext_line_number_integer" in message, label

    stored = read_line(
        database,
        insert_line(
            database, artifact_id, row_for(GLOSS_UNDER_ADJECTIVE), line_number="15"
        ),
    )
    assert stored["line_number"] == 15, (
        "INTEGER affinity turns the text '15' into the number 15, and the row records "
        "the number"
    )


def test_one_page_revision_cannot_carry_two_page_fingerprints(tmp_path: Path) -> None:
    """An oldid names one immutable revision, so it names one page text.

    Two lines of one page are two rows, and they must agree about the page they came
    from: a row that disagrees makes it impossible to say which text a citation of that
    revision was checked against. The second value is refused rather than written over
    the first, because the disagreement is the thing a person has to look at.
    """
    database = migrated(tmp_path)
    artifact_id = seed_artifact(database)
    first = insert_line(database, artifact_id, row_for(GLOSS_UNDER_ADJECTIVE))
    insert_line(database, artifact_id, row_for(INDENTED_GLOSS))

    other = fingerprint("another page text")
    message = refused(
        database,
        lambda: insert_line(
            database, artifact_id, row_for(ADJECTIVE_HEADING), page_text_sha256=other
        ),
    )
    assert "trg_source_wikitext_line_one_page_fingerprint" in message

    # The same digest under another revision, or under another declared source, is a
    # different page and stays storable.
    insert_line(
        database, artifact_id, row_for(ADJECTIVE_HEADING),
        page_revision=OTHER_OLDID, page_text_sha256=other,
    )
    insert_line(
        database, artifact_id, row_for(ADJECTIVE_HEADING),
        source_id="other-source", page_text_sha256=other,
    )

    assert read_line(database, first)["page_text_sha256"] == fingerprint(SYNTHETIC_PAGE), (
        "the recorded page fingerprint is the one that was there first"
    )
    assert len(find_line(
        database, source_id=SOURCE_ID, oldid=SYNTHETIC_OLDID, line_number=GLOSS_UNDER_ADJECTIVE
    )) == 1


def test_the_upgrade_refuses_an_inconsistent_database_before_any_ddl(
    tmp_path: Path,
) -> None:
    """A pre-existing foreign-key violation stops the revision before it creates anything.

    SQLite has no transactional DDL, so a revision that creates its table and only then
    notices the violation leaves that table behind under a version number that still
    reads the previous revision -- a half-applied migration that looks like a successful
    one to anyone who does not inspect the schema. The check runs first instead, and the
    database is left exactly as it was found.
    """
    database = migrated(tmp_path, REVISION_0011)
    seed_dangling_evidence(database)
    assert integrity(database)[1], "control: the database really is inconsistent"

    result = step(tmp_path, database, "upgrade", REVISION_0012)

    assert result.returncode != 0, "an inconsistent database must not be migrated"
    combined = f"{result.stdout}\n{result.stderr}"
    assert "外键违规" in combined
    assert CSV_EVIDENCE in combined, "the message names the rows at fault"
    assert alembic_revision(database) == REVISION_0011
    assert TABLE not in table_names(database), (
        "the check has to run before the DDL, or the table stays behind on a database "
        "whose migration aborted"
    )
    assert integrity(database)[1], "and the violation is still there to be dealt with"


def test_the_downgrade_also_refuses_an_inconsistent_database(tmp_path: Path) -> None:
    """Dropping a table does not repair a violation, so the same check runs first."""
    database = migrated(tmp_path)
    seed_dangling_evidence(database)

    result = step(tmp_path, database, "downgrade", REVISION_0011)

    assert result.returncode != 0
    assert "外键违规" in f"{result.stdout}\n{result.stderr}"
    assert TABLE in table_names(database), "nothing was dropped"
    assert alembic_revision(database) == REVISION_0012


# --- the frame around an invalid row ------------------------------------------

#: (what is wrong, the fields it changes, the constraint that must refuse it). Neither
#: a CSV row number nor a commit hash can pass as a page revision, and no row can be
#: stored without a page, a line, one line of text and a fingerprint of each.
REFUSALS: tuple[tuple[str, dict, str], ...] = (
    (
        "空的 source_id",
        {"source_id": ""},
        "ck_source_wikitext_line_source_id_present",
    ),
    (
        "source_id 带首尾空格",
        {"source_id": " zhwiktionary-pinned-oldid "},
        "ck_source_wikitext_line_source_id_trimmed",
    ),
    (
        "source_id 带定位串分隔符",
        {"source_id": "zhwiktionary:pinned"},
        "ck_source_wikitext_line_source_id_unambiguous",
    ),
    (
        "source_id 多行",
        {"source_id": "zhwiktionary\npinned"},
        "ck_source_wikitext_line_source_id_single_line",
    ),
    (
        "空 page_revision",
        {"page_revision": ""},
        "ck_source_wikitext_line_revision_present",
    ),
    (
        "page_revision 带空格",
        {"page_revision": " 9576029"},
        "ck_source_wikitext_line_revision_trimmed",
    ),
    (
        "page_revision 不是 oldid",
        {"page_revision": "9576029a"},
        "ck_source_wikitext_line_revision_digits",
    ),
    (
        "line_number 为零",
        {"line_number": 0},
        "ck_source_wikitext_line_number_positive",
    ),
    (
        "line_number 为负",
        {"line_number": -3},
        "ck_source_wikitext_line_number_positive",
    ),
    (
        "空原文",
        {"raw_text": "   "},
        "ck_source_wikitext_line_text_present",
    ),
    (
        "原文其实是多行块",
        {"raw_text": "合成释义甲\n合成释义乙"},
        "ck_source_wikitext_line_text_single_line",
    ),
    (
        "原文带 CR",
        {"raw_text": "合成释义甲\r合成释义乙"},
        "ck_source_wikitext_line_text_single_line",
    ),
    (
        "语言路径带空格",
        {"language_path": " 英語"},
        "ck_source_wikitext_line_paths_trimmed",
    ),
    (
        "标题路径不在所记语言之下",
        {"language_path": "英語", "heading_path": "法語 > 動詞"},
        "ck_source_wikitext_line_heading_path_under_language",
    ),
    (
        "记了语言但标题路径为空",
        {"language_path": "英語", "heading_path": ""},
        "ck_source_wikitext_line_heading_path_under_language",
    ),
    (
        "词性键不在闭集内",
        {"pos_heading_key": "n.", "pos_heading_text": "n."},
        "ck_source_wikitext_line_pos_heading_key",
    ),
    (
        "有词性键却没有标题文本",
        {"pos_heading_key": "adj", "pos_heading_text": ""},
        "ck_source_wikitext_line_pos_heading_agrees",
    ),
    (
        "有标题文本却没有词性键",
        {"pos_heading_key": "", "pos_heading_text": "形容詞"},
        "ck_source_wikitext_line_pos_heading_agrees",
    ),
    (
        "标题文本带空格",
        {"pos_heading_key": "adj", "pos_heading_text": " 形容詞 "},
        "ck_source_wikitext_line_pos_heading_text_trimmed",
    ),
    (
        "页面指纹过短",
        {"page_text_sha256": "a" * 63},
        "ck_source_wikitext_line_page_text_fingerprint",
    ),
    (
        "页面指纹非小写十六进制",
        {"page_text_sha256": "A" * 64},
        "ck_source_wikitext_line_page_text_fingerprint",
    ),
    (
        "行指纹带非十六进制字符",
        {"line_sha256": "z" * 64},
        "ck_source_wikitext_line_line_fingerprint",
    ),
)


@pytest.fixture(scope="module")
def refusal_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One migrated database with one artifact, shared by the refusal matrix.

    Every case below is a write the database must refuse, so no case leaves a row behind
    and sharing cannot make one test's leftovers another's failure. It exists so the
    matrix costs one migration run rather than one per case.
    """
    directory = tmp_path_factory.mktemp("refusals")
    database, env = staging(directory)
    result = run_alembic(database, "upgrade", "head", extra_env=env)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    seed_artifact(database)
    return database


def only_artifact_id(database: Path) -> int:
    connection = connect(database)
    try:
        return connection.execute(
            f"select id from {ARTIFACT_TABLE} order by id limit 1"
        ).fetchone()[0]
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("label", "overrides", "check"),
    REFUSALS,
    ids=[case[0] for case in REFUSALS],
)
def test_a_row_that_is_not_a_line_is_refused(
    refusal_database: Path, label: str, overrides: dict, check: str
) -> None:
    artifact_id = only_artifact_id(refusal_database)
    message = refused(
        refusal_database,
        lambda: insert_line(
            refusal_database, artifact_id, row_for(ADJECTIVE_HEADING), **overrides
        ),
    )
    assert check in message, f"{label}：期望 {check} 拒绝，实际 {message}"


def test_a_line_cannot_point_at_an_artifact_that_does_not_exist(tmp_path: Path) -> None:
    """The artifact reference is a real foreign key, not a number without a parent."""
    database = migrated(tmp_path)
    message = refused(
        database,
        lambda: insert_line(database, 999_999, row_for(ADJECTIVE_HEADING)),
    )
    assert "FOREIGN KEY" in message.upper()


# --- the downgrade ------------------------------------------------------------


def test_the_downgrade_removes_the_table_and_leaves_the_evidence(tmp_path: Path) -> None:
    database = migrated(tmp_path)
    evidence_id = seed_csv_evidence(database, row_locator=139)
    before_row = read_evidence(database, evidence_id)
    before_count = foreign_key_count(database)

    step(tmp_path, database, "downgrade", REVISION_0011)

    assert TABLE not in table_names(database)
    assert foreign_key_count(database) == before_count - 1
    assert read_evidence(database, evidence_id) == before_row, (
        "the downgrade drops only what this revision created"
    )
    assert alembic_revision(database) == REVISION_0011
    assert integrity(database) == ("ok", [])


def test_the_downgrade_refuses_rather_than_drop_recorded_line_evidence(
    tmp_path: Path,
) -> None:
    """Recorded evidence is not a cache, so dropping it is refused, not performed.

    The refusal has to happen *before* the first statement, or a half-downgraded
    database would be the result: the revision still reads 0012, the table is still
    there, and the row still reads what the page said.
    """
    database = migrated(tmp_path)
    artifact_id = seed_artifact(database)
    line_id = insert_line(database, artifact_id, row_for(GLOSS_UNDER_ADJECTIVE))
    before = read_line(database, line_id)

    result = step(tmp_path, database, "downgrade", REVISION_0011)

    assert result.returncode != 0, "the downgrade must not silently discard these rows"
    combined = f"{result.stdout}\n{result.stderr}"
    assert TABLE in combined, "the message names the table it refuses to drop"
    assert f"{SOURCE_ID}:{SYNTHETIC_OLDID}:{GLOSS_UNDER_ADJECTIVE}" in combined, (
        "and the positions that would be lost"
    )

    assert alembic_revision(database) == REVISION_0012
    assert tuple(read_line(database, line_id)) == tuple(before)
    assert integrity(database) == ("ok", [])


def test_the_downgrade_runs_when_nothing_was_recorded(tmp_path: Path) -> None:
    """The refusal is about losing evidence, not about the schema being irreversible.

    The rows cannot be removed to get past it -- 0012 refuses ``DELETE`` as well -- so
    the case that runs is a database where nothing was ever recorded.
    """
    database = migrated(tmp_path)
    assert TABLE in table_names(database)

    step(tmp_path, database, "downgrade", REVISION_0011)

    assert TABLE not in table_names(database)
    assert alembic_revision(database) == REVISION_0011
    assert integrity(database) == ("ok", [])
