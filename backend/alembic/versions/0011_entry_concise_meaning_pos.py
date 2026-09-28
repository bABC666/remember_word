"""Group each short study meaning under a part of speech.

Revision ID: 0010_entry_source_revision -> 0011_entry_concise_meaning_pos

Why this exists
---------------
``entry_concise_meaning`` stores "one to three short senses" per **word**. The product
rule this revision implements is one to three short senses per **part of speech**, and
the trial record for the twenty pilot words shows the difference is not cosmetic: nine
of the twenty words have two parts of speech, and ``play`` carries three verb senses
plus one noun sense. Four values cannot be stored under ``display_order between 1 and 3``
with one live row per ``(entry, display_order)``, so the current shape does not merely
read awkwardly -- it cannot hold the sample.

Shape of the change
-------------------
* six columns on ``entry_concise_meaning``: ``pos_key``, ``pos_label``, ``pos_order``,
  ``pos_source``, ``pos_evidence_locator``, ``language``;
* three snapshot columns on ``entry_concise_meaning_revision``: ``pos_key``,
  ``pos_order``, ``pos_source``, so the append-only history records the grouping that was
  in force as well as the wording;
* ``uq_entry_concise_meaning_slot`` widens from ``(lexicon_entry_id, display_order)`` to
  ``(lexicon_entry_id, pos_key, display_order)``. ``display_order`` keeps its 1..3 CHECK;
  it now means "position within the group";
* a new table ``entry_concise_meaning_citation`` for the additional source positions one
  display value may rest on.

There is deliberately **no** cap on how many groups a word may have, and none on how many
display values it may carry in total. A word's number of parts of speech is a fact about
the word, not a product quota, and a cap here would either reject a real word or silently
drop one of its groups. The only cap is the per-group one, which already existed.

How "not established yet" is stored
-----------------------------------
``pos_key`` is ``NOT NULL DEFAULT ''``, and the empty string is the one spelling of
"undetermined" -- not ``NULL``. Migration 0010 set this rule for a text-ish column and
gave the reason: ``NULL`` would be a second spelling of the same state, and every
constraint and query would then have to compare the two as equal. ``pos_source`` is the
companion column and the pair is forced to agree in both directions:
``pos_key = '' `` exactly when ``pos_source = 'none'``.

``pos_source`` is what stops a part of speech from being a bare assertion. It is either
``pos_section`` (the row's heading in the pinned revision is itself a part-of-speech
heading, and ``pos_evidence_locator`` names that heading) or ``reviewer`` (a human
judged it, and the locator names the gloss line they judged). Neither shape can be
stored without a locator, so a part of speech always says where it came from.

Refusing to guess for rows that already exist
---------------------------------------------
This revision **fails** rather than backfill whenever the database already holds a
``confirmed`` concise meaning. The new columns could be defaulted to "undetermined" and
the migration would then succeed, but that would leave a value that is *on the study
page* with no part of speech, which is the state this design exists to prevent. The
alternative -- inferring one -- is worse: the delivered zh.wiktionary source maps no
part-of-speech column at all (``columns`` in the frozen manifest are ``word``,
``meaning``, ``phonetic``), so any value chosen here would be a machine guess wearing
the shape of a fact. The operator withdraws those values with
``concise-meaning reject`` and re-proposes them with a part of speech; nothing is
deleted, because a withdrawal keeps its row and its history.

In practice this refuses nothing today: ``0009`` and ``0010`` are unreleased and the
repository's records put production at ``0007``, so no real database holds a confirmed
concise meaning yet. The check is here because the migration will outlive that fact.

SQLite mechanics
----------------
Adding a CHECK constraint requires rebuilding the table, and ``batch_alter_table`` with
``recreate="always"`` is the only way to do that. A rebuild is exactly where a foreign
key, a unique key or an index is silently lost, and a lost foreign key is invisible to
``PRAGMA foreign_key_check`` -- a constraint that was never created is never violated.
It is also where a **CHECK** constraint can be silently lost, which ``0010`` did not
assert and this revision does: SQLAlchemy reflects SQLite CHECK constraints by parsing
the table's DDL, so whether they survive depends on that parse, and the answer is
verified here rather than assumed. ``alembic/env.py`` turns foreign-key enforcement off
on the raw connection before any statement runs, because dropping the old table with
enforcement on would fire the children's ON DELETE actions and turn a rebuild into
silent data loss.

Delete semantics
----------------
Unchanged for the two existing tables. For the new one: a citation cascades with its
display value (it describes that value and means nothing without it), and is
``ON DELETE SET NULL`` on its evidence row, matching the display value's own foreign key,
so losing evidence degrades the link without deleting the citation or the value.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_entry_concise_meaning_pos"
down_revision = "0010_entry_source_revision"
branch_labels = None
depends_on = None

MEANING_TABLE = "entry_concise_meaning"
REVISION_TABLE = "entry_concise_meaning_revision"
CITATION_TABLE = "entry_concise_meaning_citation"

SLOT_INDEX = "uq_entry_concise_meaning_slot"
CITATION_ORDER_INDEX = "uq_entry_concise_meaning_citation_order"

#: name -> (type, server_default) appended to ``entry_concise_meaning``, in order.
#: Written as literals rather than imported from ``app.models``: a revision has to
#: describe the shape it created even after the models have moved on.
#:
#: ``pos_order``'s default is ``sa.text("1")`` rather than the string ``"1"`` so the DDL
#: reads ``DEFAULT 1``: a quoted default on an INTEGER column happens to work through
#: SQLite's affinity rules, but it states the wrong type and SQLite reports it back as
#: the string ``'1'``.
MEANING_COLUMNS: tuple[tuple[str, sa.types.TypeEngine, object], ...] = (
    ("pos_key", sa.String(length=24), ""),
    ("pos_label", sa.String(length=40), ""),
    ("pos_order", sa.Integer(), sa.text("1")),
    ("pos_source", sa.String(length=16), "none"),
    ("pos_evidence_locator", sa.String(length=200), ""),
    ("language", sa.String(length=16), ""),
)

#: The three snapshot columns appended to the history table.
REVISION_COLUMNS: tuple[tuple[str, sa.types.TypeEngine, object], ...] = (
    ("pos_key", sa.String(length=24), ""),
    ("pos_order", sa.Integer(), sa.text("1")),
    ("pos_source", sa.String(length=16), "none"),
)

#: The closed vocabulary, spelled out. Kept byte-identical to
#: ``models.CONCISE_MEANING_POS_KEYS`` by a test that reads both, because a value that
#: is legal in Python and illegal in the database (or the reverse) is a bug that only
#: shows up in production.
POS_KEYS = (
    "noun", "verb", "adj", "adv", "pron", "det", "num", "prep", "conj", "interj",
    "particle", "classifier", "abbrev", "prefix", "suffix", "phrase",
)
LANGUAGES = ("", "en")

POS_KEY_SQL = ", ".join(repr(key) for key in ("", *POS_KEYS))
POS_SOURCE_SQL = ", ".join(repr(source) for source in ("none", "pos_section", "reviewer"))
LANGUAGE_SQL = ", ".join(repr(language) for language in LANGUAGES)

#: CHECK constraints 0011 adds to ``entry_concise_meaning``, as (name, condition).
MEANING_CHECKS: tuple[tuple[str, str], ...] = (
    ("ck_entry_concise_meaning_pos_key", f"pos_key in ({POS_KEY_SQL})"),
    ("ck_entry_concise_meaning_pos_source", f"pos_source in ({POS_SOURCE_SQL})"),
    ("ck_entry_concise_meaning_pos_key_trimmed", "pos_key = trim(pos_key)"),
    (
        "ck_entry_concise_meaning_pos_key_matches_source",
        "(pos_key = '' AND pos_source = 'none')"
        " OR (pos_key <> '' AND pos_source <> 'none')",
    ),
    (
        "ck_entry_concise_meaning_pos_evidence",
        "(pos_source = 'none' AND length(trim(pos_evidence_locator)) = 0)"
        " OR (pos_source <> 'none' AND length(trim(pos_evidence_locator)) > 0)",
    ),
    ("ck_entry_concise_meaning_pos_order_positive", "pos_order >= 1"),
    ("ck_entry_concise_meaning_language", f"language in ({LANGUAGE_SQL})"),
    ("ck_entry_concise_meaning_language_trimmed", "language = trim(language)"),
)

#: The CHECK constraints 0009 created on the same table. A rebuild must carry them
#: through; naming them here is what lets this revision prove it did.
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

CITATION_CHECKS = frozenset(
    {
        "ck_entry_concise_meaning_citation_order_positive",
        "ck_entry_concise_meaning_citation_locator_present",
        "ck_entry_concise_meaning_citation_locator_trimmed",
    }
)

#: Every named index each table must carry, before and after the rebuild.
MEANING_INDEXES = frozenset(
    {
        "ix_entry_concise_meaning_lexicon_entry_id",
        "ix_entry_concise_meaning_source_evidence_id",
        "ix_entry_concise_meaning_status",
        SLOT_INDEX,
    }
)
REVISION_INDEXES = frozenset(
    {
        "ix_entry_concise_meaning_revision_lexicon_entry_id",
        "ix_entry_concise_meaning_revision_normalized_word",
        "ix_entry_concise_meaning_revision_concise_meaning_id",
        "ix_entry_concise_meaning_revision_action",
    }
)
CITATION_INDEXES = frozenset(
    {
        "ix_entry_concise_meaning_citation_concise_meaning_id",
        "ix_entry_concise_meaning_citation_source_evidence_id",
        CITATION_ORDER_INDEX,
    }
)

MEANING_FOREIGN_KEYS = (
    ("lexicon_entry_id", "lexicon_entry", "CASCADE"),
    ("source_evidence_id", "entry_source_evidence", "SET NULL"),
    ("confirmed_by_user_id", "user", "SET NULL"),
)
REVISION_FOREIGN_KEYS = (
    ("lexicon_entry_id", "lexicon_entry", "SET NULL"),
    ("concise_meaning_id", MEANING_TABLE, "SET NULL"),
    ("source_evidence_id", "entry_source_evidence", "SET NULL"),
    ("actor_user_id", "user", "SET NULL"),
)
CITATION_FOREIGN_KEYS = (
    ("concise_meaning_id", MEANING_TABLE, "CASCADE"),
    ("source_evidence_id", "entry_source_evidence", "SET NULL"),
)


def _foreign_key_enforcement_is_off(connection) -> bool:
    """True when the migration connection is not enforcing foreign keys.

    A table rebuild requires enforcement to be off, and the pragma cannot be changed
    from inside a migration: ``PRAGMA foreign_keys`` is a silent no-op while a
    transaction is open. ``alembic/env.py`` sets it on the raw DBAPI connection instead.
    Deliberately duplicated from ``0007``/``0010``: each revision file has to stand
    alone, and importing a helper from another revision would make this one break when
    that one is edited or removed.
    """
    return not connection.exec_driver_sql("PRAGMA foreign_keys").scalar()


def _require_enforcement_off(connection, *, direction: str) -> None:
    if _foreign_key_enforcement_is_off(connection):
        return
    raise RuntimeError(
        f"无法在 {direction} 中重建表：当前连接启用了 foreign_keys。\n"
        "SQLite 只能在重建表时增删约束，而重建过程会 drop 旧表；"
        "在外键启用状态下 drop 会触发子表的 ON DELETE 动作，造成静默数据丢失。\n"
        "PRAGMA foreign_keys 在事务内是空操作，因此必须由 alembic/env.py "
        "在 DBAPI 连接建立时关闭（见 _disable_foreign_key_enforcement）。"
    )


def _refuse_existing_confirmed_rows(connection) -> None:
    """Stop rather than give a displayed value a part of speech it never had.

    Only ``confirmed`` rows are refused. A ``candidate`` or ``rejected`` row is not on
    any page, so defaulting it to "undetermined" records the truth about it, and
    ``rejected`` rows are historical by definition.
    """
    total = connection.exec_driver_sql(
        f"select count(*) from {MEANING_TABLE} where status = 'confirmed'"
    ).scalar()
    if not total:
        return
    rows = connection.exec_driver_sql(
        f"select id, display_order, text from {MEANING_TABLE} "
        "where status = 'confirmed' order by id limit 20"
    ).fetchall()
    listed = "; ".join(f"id={row[0]}（第{row[1]}位「{row[2]}」）" for row in rows)
    raise RuntimeError(
        f"0011 upgrade 拒绝执行：{MEANING_TABLE} 里已有 {total} 条已确认（confirmed）"
        "简短释义，本迁移不会替它们猜一个词性。\n"
        f"前若干条：{listed}\n"
        "本迁移只把新列填成「未定」（pos_key='' / pos_source='none'）；那样会让"
        "已经显示在学习页上的值没有词性，而「没有词性就不该显示」正是本次设计要保证的事。\n"
        "推测一个词性更不可接受：交付的 zh.wiktionary 来源根本没有词性列"
        "（冻结 manifest 的 columns 只有 word / meaning / phonetic），任何取值都只是"
        "机器猜测被写成了事实。\n"
        "请先用 `concise-meaning reject --id <id> --note <理由>` 撤回这些值，"
        "再带词性重新提交候选并确认；撤回不会删除任何行或历史。"
    )


def _check_names(connection, table: str) -> set[str]:
    """Named CHECK constraints of one table, read from its DDL.

    SQLite has no pragma for check constraints, so the DDL is the only source. Only the
    *names* are compared: a rebuild may legitimately re-render the same condition
    differently, and the question is whether the constraint still exists at all.
    """
    ddl = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).scalar()
    if not ddl:
        raise RuntimeError(f"表 {table} 不存在，无法核对约束。")
    return {
        name
        for name in _all_known_check_names()
        if f"CONSTRAINT {name} " in ddl or f"CONSTRAINT `{name}` " in ddl
        or f'CONSTRAINT "{name}" ' in ddl
    }


def _all_known_check_names() -> set[str]:
    return (
        set(INHERITED_MEANING_CHECKS)
        | {name for name, _ in MEANING_CHECKS}
        | set(CITATION_CHECKS)
    )


def _foreign_keys(connection, table: str) -> dict[str, tuple[str, str]]:
    return {
        row[3]: (row[2], (row[6] or "NO ACTION").upper())
        for row in connection.exec_driver_sql(f'PRAGMA foreign_key_list("{table}")')
    }


def _index_names(connection, table: str) -> set[str]:
    return {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (table,)
        )
        if not row[0].startswith("sqlite_autoindex")
    }


def _unique_keys(connection, table: str) -> set[tuple[tuple[str, ...], bool]]:
    found = set()
    for row in connection.exec_driver_sql(f'PRAGMA index_list("{table}")'):
        name, is_unique = row[1], bool(row[2])
        if not name.startswith("sqlite_autoindex"):
            continue
        columns = tuple(
            item[2] for item in connection.exec_driver_sql(f'PRAGMA index_info("{name}")')
        )
        found.add((columns, is_unique))
    return found


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _verify(connection, *, direction: str) -> None:
    """Prove the rebuild kept every constraint, and that the new shape is present."""
    for table, expected_fks in (
        (MEANING_TABLE, MEANING_FOREIGN_KEYS),
        (REVISION_TABLE, REVISION_FOREIGN_KEYS),
        (CITATION_TABLE, CITATION_FOREIGN_KEYS),
    ):
        found = _foreign_keys(connection, table)
        expected = {column: (parent, rule) for column, parent, rule in expected_fks}
        _require(
            found == expected,
            f"0011 {direction} 后 {table} 的外键不符（重建可能丢约束，"
            f"而 PRAGMA foreign_key_check 查不出「从未创建」的约束）："
            f"期望 {sorted(expected.items())}，实际 {sorted(found.items())}。",
        )

    slot_columns = tuple(
        row[2]
        for row in connection.exec_driver_sql(f'PRAGMA index_info("{SLOT_INDEX}")')
    )
    _require(
        slot_columns == ("lexicon_entry_id", "pos_key", "display_order"),
        f"0011 {direction} 后 {SLOT_INDEX} 的列应为 "
        f"(lexicon_entry_id, pos_key, display_order)，实际 {slot_columns}。",
    )

    for table, expected_indexes in (
        (MEANING_TABLE, MEANING_INDEXES),
        (REVISION_TABLE, REVISION_INDEXES),
        (CITATION_TABLE, CITATION_INDEXES),
    ):
        missing = expected_indexes - _index_names(connection, table)
        _require(
            not missing,
            f"0011 {direction} 后 {table} 的索引丢失：{sorted(missing)}；"
            "重建必须保留既有索引并建立新索引。",
        )

    meaning_checks = _check_names(connection, MEANING_TABLE)
    expected_checks = set(INHERITED_MEANING_CHECKS) | {name for name, _ in MEANING_CHECKS}
    missing_checks = expected_checks - meaning_checks
    _require(
        not missing_checks,
        f"0011 {direction} 后 {MEANING_TABLE} 的 CHECK 约束丢失：{sorted(missing_checks)}。"
        "SQLite 的 CHECK 只能靠解析 DDL 反射，重建时最容易无声丢掉；"
        "丢掉任意一条都意味着产品规则不再由数据库保证。",
    )
    missing_citation_checks = CITATION_CHECKS - _check_names(connection, CITATION_TABLE)
    _require(
        not missing_citation_checks,
        f"0011 {direction} 后 {CITATION_TABLE} 的 CHECK 约束丢失："
        f"{sorted(missing_citation_checks)}。",
    )

    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    _require(
        not violations,
        f"0011 {direction} 后外键校验失败，迁移数据可能不一致：{violations[:10]}",
    )
    integrity = connection.exec_driver_sql("PRAGMA integrity_check").scalar()
    _require(integrity == "ok", f"0011 {direction} 后完整性校验失败：{integrity}")


def upgrade() -> None:
    connection = op.get_bind()
    _require_enforcement_off(connection, direction="upgrade")
    _refuse_existing_confirmed_rows(connection)

    # Dropped before the rebuild rather than inside it: the batch recreates the table
    # from its reflected shape, and the reflected shape has the *old* column list.
    op.drop_index(SLOT_INDEX, table_name=MEANING_TABLE)

    # ``recreate="always"`` because a CHECK constraint can only be added by rebuilding
    # the table; letting Alembic decide would leave a path where the columns land and
    # the constraints silently do not.
    with op.batch_alter_table(MEANING_TABLE, recreate="always") as batch:
        for name, column_type, default in MEANING_COLUMNS:
            batch.add_column(
                sa.Column(name, column_type, nullable=False, server_default=default)
            )
        for name, condition in MEANING_CHECKS:
            batch.create_check_constraint(name, condition)

    op.create_index(
        SLOT_INDEX,
        MEANING_TABLE,
        ["lexicon_entry_id", "pos_key", "display_order"],
        unique=True,
        sqlite_where=sa.text("status <> 'rejected'"),
    )

    with op.batch_alter_table(REVISION_TABLE, recreate="always") as batch:
        for name, column_type, default in REVISION_COLUMNS:
            batch.add_column(
                sa.Column(name, column_type, nullable=False, server_default=default)
            )

    op.create_table(
        CITATION_TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("concise_meaning_id", sa.Integer(), nullable=False),
        sa.Column("citation_order", sa.Integer(), nullable=False),
        sa.Column("citation_locator", sa.String(length=200), nullable=False),
        sa.Column("source_evidence_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["concise_meaning_id"], [f"{MEANING_TABLE}.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_evidence_id"], ["entry_source_evidence.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "citation_order >= 1",
            name="ck_entry_concise_meaning_citation_order_positive",
        ),
        sa.CheckConstraint(
            "length(trim(citation_locator)) > 0",
            name="ck_entry_concise_meaning_citation_locator_present",
        ),
        sa.CheckConstraint(
            "citation_locator = trim(citation_locator)",
            name="ck_entry_concise_meaning_citation_locator_trimmed",
        ),
    )
    op.create_index(
        "ix_entry_concise_meaning_citation_concise_meaning_id",
        CITATION_TABLE,
        ["concise_meaning_id"],
    )
    op.create_index(
        "ix_entry_concise_meaning_citation_source_evidence_id",
        CITATION_TABLE,
        ["source_evidence_id"],
    )
    op.create_index(
        CITATION_ORDER_INDEX,
        CITATION_TABLE,
        ["concise_meaning_id", "citation_order"],
        unique=True,
    )

    _verify(connection, direction="upgrade")
    _require_enforcement_off(connection, direction="upgrade")


def downgrade() -> None:
    """Restore the pre-0011 shape: one slot list per word, and no citation table.

    The columns are dropped, so the grouping is lost -- including which group a value
    belonged to. That is inherent: the old shape has nowhere to put it. Only ever run
    this against a throwaway copy; the production rules forbid a downgrade.
    """
    connection = op.get_bind()
    _require_enforcement_off(connection, direction="downgrade")

    op.drop_index(CITATION_ORDER_INDEX, table_name=CITATION_TABLE)
    op.drop_index(
        "ix_entry_concise_meaning_citation_source_evidence_id", table_name=CITATION_TABLE
    )
    op.drop_index(
        "ix_entry_concise_meaning_citation_concise_meaning_id", table_name=CITATION_TABLE
    )
    op.drop_table(CITATION_TABLE)

    with op.batch_alter_table(REVISION_TABLE, recreate="always") as batch:
        for name, _type, _default in REVISION_COLUMNS:
            batch.drop_column(name)

    op.drop_index(SLOT_INDEX, table_name=MEANING_TABLE)
    with op.batch_alter_table(MEANING_TABLE, recreate="always") as batch:
        for name, _condition in MEANING_CHECKS:
            batch.drop_constraint(name, type_="check")
        for name, _type, _default in MEANING_COLUMNS:
            batch.drop_column(name)
    op.create_index(
        SLOT_INDEX,
        MEANING_TABLE,
        ["lexicon_entry_id", "display_order"],
        unique=True,
        sqlite_where=sa.text("status <> 'rejected'"),
    )

    # The downgrade is verified against the *pre-0011* expectations, so it is checked
    # here rather than by the same call the upgrade makes.
    _verify_after_downgrade(connection)
    _require_enforcement_off(connection, direction="downgrade")


def _verify_after_downgrade(connection) -> None:
    direction = "downgrade"
    for table, expected_fks in (
        (MEANING_TABLE, MEANING_FOREIGN_KEYS),
        (REVISION_TABLE, REVISION_FOREIGN_KEYS),
    ):
        found = _foreign_keys(connection, table)
        expected = {column: (parent, rule) for column, parent, rule in expected_fks}
        _require(
            found == expected,
            f"0011 {direction} 后 {table} 的外键不符："
            f"期望 {sorted(expected.items())}，实际 {sorted(found.items())}。",
        )

    slot_columns = tuple(
        row[2]
        for row in connection.exec_driver_sql(f'PRAGMA index_info("{SLOT_INDEX}")')
    )
    _require(
        slot_columns == ("lexicon_entry_id", "display_order"),
        f"0011 {direction} 后 {SLOT_INDEX} 应恢复为 "
        f"(lexicon_entry_id, display_order)，实际 {slot_columns}。",
    )

    _require(
        CITATION_TABLE not in {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        },
        f"0011 {direction} 后 {CITATION_TABLE} 仍然存在。",
    )

    meaning_checks = _check_names(connection, MEANING_TABLE)
    _require(
        not (INHERITED_MEANING_CHECKS - meaning_checks),
        f"0011 {direction} 后 {MEANING_TABLE} 丢掉了 0009 的 CHECK："
        f"{sorted(INHERITED_MEANING_CHECKS - meaning_checks)}",
    )
    _require(
        MEANING_INDEXES <= _index_names(connection, MEANING_TABLE),
        f"0011 {direction} 后 {MEANING_TABLE} 的索引丢失："
        f"{sorted(MEANING_INDEXES - _index_names(connection, MEANING_TABLE))}",
    )
    _require(
        REVISION_INDEXES <= _index_names(connection, REVISION_TABLE),
        f"0011 {direction} 后 {REVISION_TABLE} 的索引丢失："
        f"{sorted(REVISION_INDEXES - _index_names(connection, REVISION_TABLE))}",
    )

    integrity = connection.exec_driver_sql("PRAGMA integrity_check").scalar()
    _require(integrity == "ok", f"0011 {direction} 后完整性校验失败：{integrity}")
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    _require(
        not violations, f"0011 {direction} 后外键校验失败：{violations[:10]}"
    )


__all__ = [
    "CITATION_TABLE",
    "MEANING_TABLE",
    "REVISION_TABLE",
    "SLOT_INDEX",
    "down_revision",
    "downgrade",
    "revision",
    "upgrade",
]
