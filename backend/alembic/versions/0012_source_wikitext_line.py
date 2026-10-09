"""Store the pinned wikitext line a citation points at.

Revision ID: 0011_entry_concise_meaning_pos -> 0012_source_wikitext_line

Why this exists
---------------
``entry_source_evidence`` records the *converted CSV* position of a value: the
physical line of ``zhwiktionary-v4en.csv``, whose ``zh_meaning`` cell aggregates
several senses of one word. The decision record
(``docs/V1.2-PHASE2.9-SOURCE-LOCATOR-POS-EVIDENCE-DECISION.md``) shows what that
cannot answer. For ``prior`` the proposed citations are ``zhwiktionary:9576029:12``,
``:15``, ``:16``, ``:19`` and ``:23`` -- line numbers in the **wikitext of the page
pinned at oldid 9576029** -- while the CSV evidence row for the same word is physical
line 139 and stores one aggregated cell. ``139`` and ``15`` are both integers and both
called "the line", they are different facts about different files, and nothing in the
schema told them apart or could look up the second one at all.

This revision adds the missing row. One row = one line of one page revision, frozen
with the fingerprint of the page it was read from, the path of headings that govern it,
and -- when the line *is* a part-of-speech heading -- the heading text and the part of
speech it stands for. It stores no wording of its own beyond the line itself, and it
binds no citation: the binding, and the confirmation rules that use it, are a later
slice.

How a wikitext line differs from a CSV row
------------------------------------------
The two positions are told apart by their keys, not by a prefix on a string:

* CSV row -- ``entry_source_evidence``: identity is ``(evidence_sha256, import_run_id)``;
  the position is ``row_locator`` inside the artifact's *converted bytes*, and the row
  can be re-adjudicated by a later run without touching the earlier decision.
* Wikitext line -- ``source_wikitext_line`` here: identity is
  ``(source_id, page_revision, line_number)``; the position is a line of the *page
  revision* named by ``page_revision``, and a second row for the same triple is refused
  because one revision of one page has exactly one line 15.

The distinction is enforced rather than documented. ``page_revision`` must be a
non-empty run of digits -- an oldid -- and ``raw_text`` must be a single line with no
CR or LF in it. A converted-CSV position has neither a page revision nor a single-line
body: its cell is one field of a delimited record, and ``row_locator`` counts the file's
physical lines including the header. So a CSV row number cannot be stored here as
though it were a line of wikitext, and ``page_text_sha256`` -- the digest of the whole
pinned page -- has no counterpart in the CSV world at all.

Shape of the change
-------------------
One table, ``source_wikitext_line``, plus two indexes. Nothing existing is altered:
no column is added to ``entry_source_evidence``, no row is backfilled, and no other
table's constraints move.

How "not established" is stored
-------------------------------
``language_path``, ``heading_path``, ``pos_heading_key`` and ``pos_heading_text`` are
``NOT NULL DEFAULT ''``, and ``''`` is the one spelling of "nothing was established" --
not ``NULL``. Migration 0010 set this rule for a text-ish column and gave the reason:
``NULL`` would be a second spelling of the same state, and every constraint and query
would then have to compare the two as equal.

``pos_heading_key`` and ``pos_heading_text`` are forced to agree in both directions
(the pattern ``0011`` used for ``pos_key``/``pos_source``): an empty key means this line
is not a part-of-speech heading and then there is no heading text either, while a stated
key must name the heading it was read from. A part of speech can therefore never be
recorded here as a bare assertion.

``language_path`` answers "which language section is this line in"; ``heading_path``
answers "which headings govern it", and the two are joined by ``' > '``. When a
language is recorded, the heading path must be that same string or start with it
followed by the separator, so a row cannot claim to be in one language's section while
its heading path says another. A language may be left unrecorded while headings are,
because the honest answer for a page lead or an undated section is "not determined" --
what is *not* allowed is recording a language the paths contradict. Carrying both means
a reader never has to guess which of the two facts a section heading is.

Boundary of the constraints
---------------------------
SQLite cannot check one table against another, so the database cannot prove that
``source_artifact_id`` points at a preserved *wikitext* artifact rather than at a
converted CSV, nor that ``page_revision`` names a revision that upstream really had,
nor that ``page_text_sha256``/``line_sha256`` are the digests a re-read of the archive
would produce -- a fingerprint is stored, compared and recomputed, never verified by
SQLite. What it does prove is the shape: a declared source id, a page revision of
digits, an **integer** line number, one single-line body, paths that agree with each
other, a part of speech from the closed vocabulary, two 64-character lowercase-hex
fingerprints, and the three row-level rules the triggers below enforce. Matching the
artifact to the declared source, resolving the locator's short name to a declared source
id, and refusing a citation whose wording is not in the cited line at the confirmation
entry are the next slice's rules, and they need this row to exist first.

Only the ``line_number`` column carries a ``typeof`` check, and the reason is narrow:
SQLite enforces a column's declared type by *affinity*, not by rejection. An INTEGER
column keeps text that does not look like a number as text, so ``'abc'`` would sit in a
column every reader treats as a line number and compare above every real line (SQLite
orders INTEGER before TEXT); a REAL is kept too when it is not a whole number. The text
columns need no equivalent: TEXT affinity already converts a stored number to its text
form, so what a reader compares is what is stored.

``raw_text`` is deliberately **not** trimmed and carries no length cap. Leading
whitespace and list markers are meaningful in wikitext (``#`` numbered glosses, ``:``
indentation), so trimming it would rewrite the evidence into something the source never
said; and a "line" that must stay verbatim cannot be truncated.

The four row-level rules this revision enforces in the database
--------------------------------------------------------------
Only one of the four is a shape a ``CHECK`` can state. A ``CHECK`` sees just the row it
is given: it cannot compare an incoming row with the rows already stored, and "this row
may never be updated or deleted" is not a shape of a row at all. The other three are
therefore triggers -- the first in this schema -- and each one is named, asserted present
by the verification below, and covered by a test that attempts the write it must refuse.

* ``line_number`` must be an **integer**: ``typeof(line_number) = 'integer'``. See
  above -- without it a stored line number may be text, and the position stops meaning
  what every reader assumes it means.
* **No UPDATE and no DELETE.** A recorded line is evidence, not a cache: it is what the
  page said at that revision, and the whole point of copying it into the database is
  that the answer survives the archive being moved, re-packed or lost. An in-place edit
  would leave a row that claims to be a source reading while holding a rewriting of it,
  and ``line_sha256`` would then disagree with the row's own fields -- but only for a
  reader who thinks to recompute it. Both statements are refused outright, so the
  correction of a wrong line has to be a new row plus a withdrawn citation, which is a
  recorded decision rather than a silent edit. Each refusal names its trigger, because
  SQLite reports only the message a ``RAISE`` was given.
* **One page revision, one page fingerprint.** ``oldid`` names an immutable revision
  upstream, so two lines of the same ``(source_id, page_revision)`` cannot have been
  read from two different texts: one of the two ``page_text_sha256`` values must be
  wrong, and a citation resolved against the page would be checked against the wrong
  one. The trigger refuses the *second* value when it disagrees with the one already
  recorded for that revision, whatever line it arrives on. It does not merge or
  overwrite: disagreement is the thing being surfaced.

Checking an inconsistent database *before* touching it
------------------------------------------------------
The upgrade first asks ``PRAGMA foreign_key_check`` whether the database it is about to
change is consistent, and stops if it is not. This is deliberately *before* the DDL
rather than after it: SQLite has no transactional DDL, so a revision that creates a
table and only then discovers a pre-existing violation leaves that table behind -- a
half-applied revision whose aborted run is indistinguishable from a completed one, and
whose violation was not even this revision's doing. Refusing first means an inconsistent
database is left exactly as it was found, with the violating rows named in the message.
The check refuses nothing in practice: every database in this repository is consistent,
and the migration's own connection runs with foreign-key *enforcement* off
(``alembic/env.py``), which is why the violation is possible in the first place and why
it has to be asked about explicitly.

``PRAGMA integrity_check`` stays where it was, as a post-condition of the DDL this
revision runs; the foreign-key check is the one that had to move.

Delete semantics
----------------
``source_artifact_id`` is ``ON DELETE RESTRICT``, matching
``entry_source_evidence.source_artifact_id``: a preserved file that a line was read from
must not be removable out from under the evidence. Nothing cascades into this table.
The downgrade therefore refuses to run while any row exists rather than dropping
recorded line evidence, the same choice ``0011`` makes for the facts it cannot store
again.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_source_wikitext_line"
down_revision = "0011_entry_concise_meaning_pos"
branch_labels = None
depends_on = None

TABLE = "source_wikitext_line"
ARTIFACT_TABLE = "source_artifact"

POSITION_INDEX = "uq_source_wikitext_line_position"
ARTIFACT_INDEX = "ix_source_wikitext_line_source_artifact_id"

#: The position of one line of one page revision. ``source_id`` is in the key because
#: an ``oldid`` is only unique inside the wiki that issued it: without it, a second
#: source that happens to use the same number would collide with the first.
POSITION_COLUMNS = ("source_id", "page_revision", "line_number")

#: The closed vocabulary of part-of-speech keys, written out as literals rather than
#: imported from ``app.models``: a revision has to describe the shape it created even
#: after the models have moved on. Kept byte-identical to
#: ``models.CONCISE_MEANING_POS_KEYS`` by a test that reads both -- the heading basis a
#: reader resolves must be the same closed set the display grouping uses, or a heading
#: could establish a part of speech the study page has no group for.
POS_KEYS = (
    "noun", "verb", "adj", "adv", "pron", "det", "num", "prep", "conj", "interj",
    "particle", "classifier", "abbrev", "prefix", "suffix", "phrase",
)

POS_KEY_SQL = ", ".join(repr(key) for key in ("", *POS_KEYS))

#: The separator both path columns are joined with, spelled once. It is the same
#: ``' > '`` the extraction index writes in its ``section`` field, so a stored row and
#: the record it was derived from read as the same string.
PATH_SEPARATOR = " > "

#: Every CHECK this revision creates, as (name, condition). Iterated by both
#: ``upgrade`` and the verification below, so a rule cannot be present in one and
#: missing from the other.
CHECKS: tuple[tuple[str, str], ...] = (
    # --- the declared source ------------------------------------------------
    ("ck_source_wikitext_line_source_id_present", "length(source_id) > 0"),
    # Stored already trimmed, so two rows cannot differ by invisible whitespace while
    # naming the same source.
    ("ck_source_wikitext_line_source_id_trimmed", "source_id = trim(source_id)"),
    # The locator's separator must not appear in the source id. A locator is
    # ``source_id:revision:line``; an id containing ``:`` would make the string
    # ambiguous, and the decision record's rule is that a source is resolved through a
    # declared alias rather than by reading a prefix off the string.
    ("ck_source_wikitext_line_source_id_unambiguous", "instr(source_id, ':') = 0"),
    # The source id is one of the fields joined into ``line_sha256``, and that
    # serialization is only unambiguous while no field can contain a line break.
    (
        "ck_source_wikitext_line_source_id_single_line",
        "instr(source_id, char(10)) = 0 AND instr(source_id, char(13)) = 0",
    ),
    # --- the page revision and the line -------------------------------------
    ("ck_source_wikitext_line_revision_present", "length(page_revision) > 0"),
    (
        "ck_source_wikitext_line_revision_trimmed",
        "page_revision = trim(page_revision)",
    ),
    # An oldid is a run of digits. This is also what stops a CSV row number, a commit
    # hash or a version string from being stored as a page revision: the column cannot
    # hold a value that is not an oldid, so a line here is a line of a *page*.
    (
        "ck_source_wikitext_line_revision_digits",
        "page_revision NOT GLOB '*[^0-9]*'",
    ),
    ("ck_source_wikitext_line_number_positive", "line_number >= 1"),
    # ...and a *number*. SQLite keeps text that does not look numeric in an INTEGER
    # column, and it sorts every INTEGER before every TEXT, so a stored ``'abc'`` would
    # compare as "after line 15" while ``length``, ordering and every reader treat the
    # column as a position. ``'15'`` is stored as the integer 15 by affinity and passes,
    # which is the correct answer: what is stored is a number.
    (
        "ck_source_wikitext_line_number_integer",
        "typeof(line_number) = 'integer'",
    ),
    # --- the line's own text ------------------------------------------------
    # A cited line with nothing in it proves nothing, so the empty row is not
    # representable. Deliberately not trimmed: wikitext indentation is content.
    ("ck_source_wikitext_line_text_present", "length(trim(raw_text)) > 0"),
    # One line, literally. A CR or LF would make the stored text a block rather than a
    # line, and the line number would then no longer say which text is meant.
    (
        "ck_source_wikitext_line_text_single_line",
        "instr(raw_text, char(10)) = 0 AND instr(raw_text, char(13)) = 0",
    ),
    # --- the paths ----------------------------------------------------------
    (
        "ck_source_wikitext_line_paths_trimmed",
        "language_path = trim(language_path) AND heading_path = trim(heading_path)",
    ),
    (
        "ck_source_wikitext_line_paths_single_line",
        (
            "instr(language_path, char(10)) = 0 AND instr(language_path, char(13)) = 0"
            " AND instr(heading_path, char(10)) = 0 AND instr(heading_path, char(13)) = 0"
        ),
    ),
    # When a language is recorded, the heading path is either that language itself or
    # a sub-path under it. ``substr`` rather than ``LIKE``: a path may legitimately
    # contain ``%`` or ``_``, and a pattern match would treat them as wildcards and
    # accept a heading path that is not under the recorded language at all.
    (
        "ck_source_wikitext_line_heading_path_under_language",
        (
            "length(language_path) = 0"
            " OR heading_path = language_path"
            " OR substr(heading_path, 1, length(language_path) + 3)"
            f" = language_path || '{PATH_SEPARATOR}'"
        ),
    ),
    # --- the part-of-speech heading basis -----------------------------------
    (
        "ck_source_wikitext_line_pos_heading_key",
        f"pos_heading_key in ({POS_KEY_SQL})",
    ),
    (
        "ck_source_wikitext_line_pos_heading_key_trimmed",
        "pos_heading_key = trim(pos_heading_key)",
    ),
    (
        "ck_source_wikitext_line_pos_heading_text_trimmed",
        "pos_heading_text = trim(pos_heading_text)",
    ),
    # The key and the heading text stand or fall together: no key without the heading
    # it was read from, and no heading text that claims no part of speech. This is what
    # makes "a part of speech with no basis" unrepresentable here.
    (
        "ck_source_wikitext_line_pos_heading_agrees",
        (
            "(pos_heading_key = '' AND length(pos_heading_text) = 0)"
            " OR (pos_heading_key <> '' AND length(pos_heading_text) > 0)"
        ),
    ),
    (
        "ck_source_wikitext_line_pos_heading_text_single_line",
        (
            "instr(pos_heading_text, char(10)) = 0"
            " AND instr(pos_heading_text, char(13)) = 0"
        ),
    ),
    # --- the fingerprints ---------------------------------------------------
    # Lowercase hex, exactly 64 characters. A truncated digest, or one in another
    # alphabet, cannot be compared byte-for-byte with a digest recomputed from the
    # preserved page -- and a fingerprint that cannot be compared is not a fingerprint.
    (
        "ck_source_wikitext_line_page_text_fingerprint",
        (
            "length(page_text_sha256) = 64"
            " AND page_text_sha256 NOT GLOB '*[^0-9a-f]*'"
        ),
    ),
    (
        "ck_source_wikitext_line_line_fingerprint",
        "length(line_sha256) = 64 AND line_sha256 NOT GLOB '*[^0-9a-f]*'",
    ),
)

#: Every named index the table must carry, verified after creation.
EXPECTED_INDEXES = frozenset({POSITION_INDEX, ARTIFACT_INDEX})

#: The triggers this revision creates, as ``(name, SQL)``. SQLite has no other way to
#: state these three rules: a CHECK sees only its own row, and "this row may never be
#: updated or deleted" is not a shape of a row at all. Written out rather than generated,
#: so a reader of this revision can see the exact statement the database runs.
#:
#: Each refusal message begins with its own trigger name. SQLite reports a failed CHECK
#: as ``CHECK constraint failed: <name>`` but a ``RAISE`` reports only the text it was
#: given, so the name is written into the message: an operator who sees the refusal has
#: to learn which rule refused it, not only that one did.
TRIGGERS: tuple[tuple[str, str], ...] = (
    (
        "trg_source_wikitext_line_no_update",
        (
            f"CREATE TRIGGER trg_source_wikitext_line_no_update\n"
            f"BEFORE UPDATE ON {TABLE}\n"
            "BEGIN\n"
            "    SELECT RAISE(ABORT, 'trg_source_wikitext_line_no_update: "
            "source_wikitext_line 是只追加的逐行证据，不允许 UPDATE。"
            "改写一行会让它继续声称是来源原文；要更正请另写一行证据，"
            "并撤回引用它的结论。');\n"
            "END"
        ),
    ),
    (
        "trg_source_wikitext_line_no_delete",
        (
            f"CREATE TRIGGER trg_source_wikitext_line_no_delete\n"
            f"BEFORE DELETE ON {TABLE}\n"
            "BEGIN\n"
            "    SELECT RAISE(ABORT, 'trg_source_wikitext_line_no_delete: "
            "source_wikitext_line 是只追加的逐行证据，不允许 DELETE。"
            "这一行是「某页某一行当时是什么」的唯一在库记录，"
            "删掉它无法从任何地方重新推导出来。');\n"
            "END"
        ),
    ),
    (
        "trg_source_wikitext_line_one_page_fingerprint",
        (
            f"CREATE TRIGGER trg_source_wikitext_line_one_page_fingerprint\n"
            f"BEFORE INSERT ON {TABLE}\n"
            "WHEN EXISTS (\n"
            f"    SELECT 1 FROM {TABLE}\n"
            "    WHERE source_id = NEW.source_id\n"
            "      AND page_revision = NEW.page_revision\n"
            "      AND page_text_sha256 <> NEW.page_text_sha256\n"
            ")\n"
            "BEGIN\n"
            "    SELECT RAISE(ABORT, 'trg_source_wikitext_line_one_page_fingerprint: "
            "同一来源、同一固定修订只能有一个页面原文指纹：该 oldid 已记录了另一个 "
            "page_text_sha256。同一个 oldid 不可能有两个版本的原文，其中一个必错；"
            "请先确认哪一份才是该修订的原文。');\n"
            "END"
        ),
    ),
)

#: The trigger names the verification below requires to be present.
EXPECTED_TRIGGERS = frozenset(name for name, _sql in TRIGGERS)

#: column -> (parent table, ON DELETE rule) the table must declare.
EXPECTED_FOREIGN_KEYS: tuple[tuple[str, str, str], ...] = (
    ("source_artifact_id", ARTIFACT_TABLE, "RESTRICT"),
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _table_exists(connection) -> bool:
    return bool(
        connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
        ).scalar()
    )


def _check_names(connection) -> set[str]:
    """Named CHECK constraints of the table, read from its DDL.

    SQLite has no pragma for check constraints, so the DDL is the only source. Only
    the *names* are compared: the question is whether the rule still exists at all.
    """
    ddl = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
    ).scalar()
    _require(bool(ddl), f"表 {TABLE} 不存在，无法核对约束。")
    return {
        name
        for name, _condition in CHECKS
        if f"CONSTRAINT {name} " in ddl
        or f"CONSTRAINT `{name}` " in ddl
        or f'CONSTRAINT "{name}" ' in ddl
    }


def _foreign_keys(connection) -> dict[str, tuple[str, str]]:
    return {
        row[3]: (row[2], (row[6] or "NO ACTION").upper())
        for row in connection.exec_driver_sql(f'PRAGMA foreign_key_list("{TABLE}")')
    }


def _index_names(connection) -> set[str]:
    return {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (TABLE,)
        )
        if not row[0].startswith("sqlite_autoindex")
    }


def _index_columns(connection, index: str) -> tuple[str, ...]:
    return tuple(
        row[2] for row in connection.exec_driver_sql(f'PRAGMA index_info("{index}")')
    )


def _trigger_names(connection) -> set[str]:
    return {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (TABLE,)
        )
    }


def _require_consistent_database(connection, *, direction: str) -> None:
    """Refuse to touch a database that already violates a foreign key.

    Called *before* the DDL, not after it. SQLite has no transactional DDL, so a
    revision that creates first and checks later leaves its table behind when the check
    fails -- a half-applied revision that looks the same as a completed one to anyone
    reading ``alembic_version``. Asking first means an inconsistent database is left
    exactly as it was found, and the rows at fault are named so the operator can look at
    them instead of guessing which revision was responsible.
    """
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    _require(
        not violations,
        f"0012 {direction} 拒绝执行：数据库当前已存在外键违规，本修订不会在它之上建表。\n"
        f"违规（表, rowid, 父表, 约束序号）：{violations[:10]}\n"
        "SQLite 没有事务性 DDL：先建表再检查会让这张表在失败后留下来，"
        "版本号却仍停在上一版，事后无法与「迁移成功」区分。\n"
        "请先修复或明确处置这些行，再执行本迁移。",
    )


def _verify(connection, *, direction: str) -> None:
    """Prove the table, its constraints, its triggers and its indexes are there."""
    _require(_table_exists(connection), f"0012 {direction} 后 {TABLE} 不存在。")

    found = _foreign_keys(connection)
    expected = {column: (parent, rule) for column, parent, rule in EXPECTED_FOREIGN_KEYS}
    _require(
        found == expected,
        f"0012 {direction} 后 {TABLE} 的外键不符：期望 {sorted(expected.items())}，"
        f"实际 {sorted(found.items())}。",
    )

    missing_indexes = EXPECTED_INDEXES - _index_names(connection)
    _require(
        not missing_indexes,
        f"0012 {direction} 后 {TABLE} 的索引丢失：{sorted(missing_indexes)}。",
    )

    position_columns = _index_columns(connection, POSITION_INDEX)
    _require(
        position_columns == POSITION_COLUMNS,
        f"0012 {direction} 后 {POSITION_INDEX} 的列应为 {POSITION_COLUMNS}，"
        f"实际 {position_columns}。缺少任意一列都会让同一位置可以被记录两次，"
        "或让两个来源的同一 oldid 互相冲突。",
    )
    unique = [
        bool(row[2])
        for row in connection.exec_driver_sql(f'PRAGMA index_list("{TABLE}")')
        if row[1] == POSITION_INDEX
    ]
    _require(
        unique == [True],
        f"0012 {direction} 后 {POSITION_INDEX} 不是唯一索引（实际 {unique}）："
        "同一来源、同一固定修订的同一行号必须只能有一行。",
    )

    missing_checks = {name for name, _condition in CHECKS} - _check_names(connection)
    _require(
        not missing_checks,
        f"0012 {direction} 后 {TABLE} 的 CHECK 约束丢失：{sorted(missing_checks)}。"
        "SQLite 的 CHECK 只能靠解析 DDL 反射；丢掉任意一条都意味着「这一行是什么」"
        "不再由数据库保证。",
    )

    missing_triggers = EXPECTED_TRIGGERS - _trigger_names(connection)
    _require(
        not missing_triggers,
        f"0012 {direction} 后 {TABLE} 的触发器丢失：{sorted(missing_triggers)}。"
        "只追加与「同一修订一个页面指纹」这三条规则只能由触发器表达，"
        "丢掉它们不会有任何 pragma 报告：一张可以随便改写和删除的证据表，"
        "读起来与一张不可改写的证据表完全一样。",
    )

    integrity = connection.exec_driver_sql("PRAGMA integrity_check").scalar()
    _require(integrity == "ok", f"0012 {direction} 后完整性校验失败：{integrity}")


def upgrade() -> None:
    connection = op.get_bind()
    # Before the first statement, so a database that is already inconsistent is left
    # exactly as it was found. See ``_require_consistent_database``.
    _require_consistent_database(connection, direction="upgrade")

    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_artifact_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("page_revision", sa.String(length=64), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        # No server default: an evidence row whose text nobody stated is not a row this
        # table is for, so the column has to be named by every writer.
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("language_path", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("heading_path", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("pos_heading_key", sa.String(length=24), nullable=False, server_default=""),
        sa.Column("pos_heading_text", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("page_text_sha256", sa.String(length=64), nullable=False),
        sa.Column("line_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["source_artifact_id"], [f"{ARTIFACT_TABLE}.id"], ondelete="RESTRICT"
        ),
        *[sa.CheckConstraint(condition, name=name) for name, condition in CHECKS],
    )
    op.create_index(ARTIFACT_INDEX, TABLE, ["source_artifact_id"])
    op.create_index(POSITION_INDEX, TABLE, list(POSITION_COLUMNS), unique=True)

    # After the table and its indexes: a trigger cannot name a column of a table that
    # does not exist yet. Unlike the foreign-key check above, these statements cannot
    # fail on data -- they add rules rather than test rows.
    for _name, statement in TRIGGERS:
        op.execute(statement)

    _verify(op.get_bind(), direction="upgrade")


def _refuse_lossy_downgrade(connection) -> None:
    """Refuse while any line evidence is recorded, before dropping anything.

    The table is empty in every database that exists today, so this refuses nothing in
    practice -- and it is here because the rows this table will hold are exactly the
    evidence the design exists to keep. Dropping them would not fail any constraint:
    the artifact would still be on disk, and no later query could tell that a recorded
    line had ever been read.
    """
    total = connection.exec_driver_sql(f"select count(*) from {TABLE}").scalar()
    if not total:
        return
    rows = connection.exec_driver_sql(
        f"select source_id, page_revision, line_number from {TABLE} "
        "order by source_id, page_revision, line_number limit 10"
    ).fetchall()
    listed = ", ".join(f"{row[0]}:{row[1]}:{row[2]}" for row in rows)
    raise RuntimeError(
        f"0012 downgrade 拒绝执行：{TABLE} 里已有 {total} 行固定修订逐行证据。\n"
        f"前若干处位置：{listed}\n"
        "降级会 drop 该表，等于丢弃已经记录的逐行证据；这些行不是可重新推导的缓存，"
        "而是「某一页的某一行当时是什么」的唯一在库记录，工件离开仓库后就再也查不回来。\n"
        "请先把这些证据导出留档，或明确同意丢弃它们之后，再执行降级。"
    )


def downgrade() -> None:
    """Drop the table this revision created, and nothing else.

    Exact in the other direction because nothing existing was altered: no column was
    added to another table, so there is no rebuild and no inherited constraint that
    could be lost. The table's triggers go with it -- SQLite drops a table's triggers
    with the table, which is also why nothing here drops them by name.

    The same foreign-key pre-flight runs as in the upgrade: dropping a table does not
    repair a database that was already inconsistent, and this revision should not be the
    one that appears to have changed it.
    """
    connection = op.get_bind()
    _require_consistent_database(connection, direction="downgrade")
    _refuse_lossy_downgrade(connection)

    op.drop_index(POSITION_INDEX, table_name=TABLE)
    op.drop_index(ARTIFACT_INDEX, table_name=TABLE)
    op.drop_table(TABLE)

    _require(
        not _table_exists(connection),
        f"0012 downgrade 后 {TABLE} 仍然存在。",
    )
    _require(
        not _trigger_names(connection),
        f"0012 downgrade 后 {TABLE} 的触发器仍然存在：{sorted(_trigger_names(connection))}。",
    )
    integrity = connection.exec_driver_sql("PRAGMA integrity_check").scalar()
    _require(integrity == "ok", f"0012 downgrade 后完整性校验失败：{integrity}")


__all__ = [
    "ARTIFACT_INDEX",
    "CHECKS",
    "EXPECTED_TRIGGERS",
    "POSITION_COLUMNS",
    "POSITION_INDEX",
    "POS_KEYS",
    "TABLE",
    "TRIGGERS",
    "down_revision",
    "downgrade",
    "revision",
    "upgrade",
]
