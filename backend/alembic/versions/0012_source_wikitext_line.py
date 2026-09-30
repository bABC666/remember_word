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
converted CSV, nor that ``page_revision`` names a revision that upstream really had.
What it does prove is the shape: a declared source id, a page revision of digits, a
positive line number, one single-line body, paths that agree with each other, a part of
speech from the closed vocabulary, and two 64-character lowercase-hex fingerprints.
Matching the artifact to the declared source, resolving the locator's short name to a
declared source id, and refusing a citation whose wording is not in the cited line at
the confirmation entry are the next slice's rules, and they need this row to exist
first.

``raw_text`` is deliberately **not** trimmed and carries no length cap. Leading
whitespace and list markers are meaningful in wikitext (``#`` numbered glosses, ``:``
indentation), so trimming it would rewrite the evidence into something the source never
said; and a "line" that must stay verbatim cannot be truncated.

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


def _verify(connection, *, direction: str) -> None:
    """Prove the table, its constraints and its indexes are physically there."""
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

    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    _require(
        not violations,
        f"0012 {direction} 后外键校验失败，迁移数据可能不一致：{violations[:10]}",
    )
    integrity = connection.exec_driver_sql("PRAGMA integrity_check").scalar()
    _require(integrity == "ok", f"0012 {direction} 后完整性校验失败：{integrity}")


def upgrade() -> None:
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
    could be lost.
    """
    connection = op.get_bind()
    _refuse_lossy_downgrade(connection)

    op.drop_index(POSITION_INDEX, table_name=TABLE)
    op.drop_index(ARTIFACT_INDEX, table_name=TABLE)
    op.drop_table(TABLE)

    _require(
        not _table_exists(connection),
        f"0012 downgrade 后 {TABLE} 仍然存在。",
    )
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    _require(not violations, f"0012 downgrade 后外键校验失败：{violations[:10]}")
    integrity = connection.exec_driver_sql("PRAGMA integrity_check").scalar()
    _require(integrity == "ok", f"0012 downgrade 后完整性校验失败：{integrity}")


__all__ = [
    "ARTIFACT_INDEX",
    "CHECKS",
    "POSITION_COLUMNS",
    "POSITION_INDEX",
    "POS_KEYS",
    "TABLE",
    "down_revision",
    "downgrade",
    "revision",
    "upgrade",
]
