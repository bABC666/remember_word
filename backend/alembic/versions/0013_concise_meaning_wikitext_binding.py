"""Bind each short-meaning position to an optional pinned wikitext line.

Revision ID: 0013_concise_meaning_wikitext_binding
Revises: 0012_source_wikitext_line

The three nullable foreign keys are deliberately separate: a primary gloss, an
additional citation, and a part-of-speech basis can name three different lines.
Existing locator strings and CSV evidence IDs remain in place. NULL means no
verified line binding; no old locator is parsed or guessed during this migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_concise_meaning_wikitext_binding"
down_revision = "0012_source_wikitext_line"
branch_labels = None
depends_on = None

MEANING = "entry_concise_meaning"
CITATION = "entry_concise_meaning_citation"
LINE = "source_wikitext_line"
BINDINGS = (
    (MEANING, "primary_wikitext_line_id"),
    (MEANING, "pos_wikitext_line_id"),
    (CITATION, "wikitext_line_id"),
)
TRIGGER = "trg_entry_concise_meaning_wikitext_binding_shape"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _foreign_keys(connection, table: str) -> dict[str, tuple[str, str]]:
    return {
        row[3]: (row[2], (row[6] or "NO ACTION").upper())
        for row in connection.exec_driver_sql(f'PRAGMA foreign_key_list("{table}")')
    }


def _indexes(connection, table: str) -> dict[str, tuple[tuple[str, ...], bool, bool]]:
    found = {}
    for row in connection.exec_driver_sql(f'PRAGMA index_list("{table}")'):
        if row[1].startswith("sqlite_autoindex"):
            continue
        columns = tuple(
            item[2] for item in connection.exec_driver_sql(f'PRAGMA index_info("{row[1]}")')
        )
        found[row[1]] = (columns, bool(row[2]), bool(row[4]))
    return found


def _checks(connection, table: str) -> set[str]:
    inspector = sa.inspect(connection)
    return {item["name"] for item in inspector.get_check_constraints(table)}


def _snapshot(connection) -> dict[str, tuple[dict, dict, set]]:
    return {
        table: (_foreign_keys(connection, table), _indexes(connection, table),
                _checks(connection, table))
        for table in (MEANING, CITATION)
    }


def _consistent(connection, direction: str) -> None:
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    _require(not violations, f"0013 {direction} 拒绝：已有外键违规 {violations[:10]}")


def _verify(connection, before: dict, *, upgrade: bool) -> None:
    for table, (old_fks, old_indexes, old_checks) in before.items():
        expected_fks = dict(old_fks)
        expected_indexes = dict(old_indexes)
        if upgrade:
            for owner, column in BINDINGS:
                if owner == table:
                    expected_fks[column] = (LINE, "RESTRICT")
                    expected_indexes[f"ix_{table}_{column}"] = ((column,), False, False)
        _require(_foreign_keys(connection, table) == expected_fks,
                 f"0013 {table} 外键丢失或变化")
        _require(_indexes(connection, table) == expected_indexes,
                 f"0013 {table} 索引丢失或变化")
        _require(_checks(connection, table) == old_checks,
                 f"0013 {table} 原有 CHECK 约束丢失或变化")
    _consistent(connection, "verify")
    _require(connection.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok",
             "0013 完整性校验失败")


def _create_shape_triggers() -> None:
    # Preserve the older supplement/no-source and undetermined/no-basis rules when
    # these new paths are used. The original CHECKs continue to guard their columns.
    for operation in ("INSERT", "UPDATE"):
        name = f"{TRIGGER}_{operation.lower()}"
        op.execute(
            f"CREATE TRIGGER {name} BEFORE {operation} ON {MEANING} BEGIN "
            "SELECT CASE WHEN NEW.provenance_kind = 'ai_supplement' "
            "AND NEW.primary_wikitext_line_id IS NOT NULL "
            f"THEN RAISE(ABORT, '{name}: supplement has no primary source') "
            "WHEN NEW.pos_source = 'none' AND NEW.pos_wikitext_line_id IS NOT NULL "
            f"THEN RAISE(ABORT, '{name}: undetermined POS has no line basis') "
            "END; END"
        )


def upgrade() -> None:
    connection = op.get_bind()
    _consistent(connection, "upgrade")
    _require(not connection.exec_driver_sql("PRAGMA foreign_keys").scalar(),
             "0013 upgrade 拒绝：重建表前必须关闭外键执行")
    before = _snapshot(connection)
    for table in (CITATION, MEANING):
        with op.batch_alter_table(table, recreate="always") as batch:
            for owner, column in BINDINGS:
                if owner != table:
                    continue
                batch.add_column(sa.Column(column, sa.Integer(), nullable=True))
                batch.create_foreign_key(
                    f"fk_{table}_{column}", LINE, [column], ["id"], ondelete="RESTRICT"
                )
    for table, column in BINDINGS:
        op.create_index(f"ix_{table}_{column}", table, [column])
    _create_shape_triggers()
    _verify(connection, before, upgrade=True)


def _refuse_lossy_downgrade(connection) -> None:
    occupied = []
    for table, column in BINDINGS:
        ids = connection.exec_driver_sql(
            f"SELECT id FROM {table} WHERE {column} IS NOT NULL ORDER BY id LIMIT 10"
        ).fetchall()
        if ids:
            occupied.append(f"{table}.{column}: {[row[0] for row in ids]}")
    _require(not occupied, "0013 downgrade 拒绝：存在固定 wikitext 行绑定，"
             "降级会丢失这些引用；" + "; ".join(occupied))


def downgrade() -> None:
    connection = op.get_bind()
    _consistent(connection, "downgrade")
    _refuse_lossy_downgrade(connection)  # before the first DDL statement
    _require(not connection.exec_driver_sql("PRAGMA foreign_keys").scalar(),
             "0013 downgrade 拒绝：重建表前必须关闭外键执行")
    before = _snapshot(connection)
    for operation in ("INSERT", "UPDATE"):
        op.execute(f"DROP TRIGGER {TRIGGER}_{operation.lower()}")
    for table, column in BINDINGS:
        op.drop_index(f"ix_{table}_{column}", table_name=table)
    with op.batch_alter_table(CITATION, recreate="always") as batch:
        batch.drop_column("wikitext_line_id")
    with op.batch_alter_table(MEANING, recreate="always") as batch:
        batch.drop_column("primary_wikitext_line_id")
        batch.drop_column("pos_wikitext_line_id")
    expected = {
        table: (
            {key: value for key, value in fks.items() if key not in
             {column for owner, column in BINDINGS if owner == table}},
            {key: value for key, value in indexes.items() if key not in
             {f"ix_{table}_{column}" for owner, column in BINDINGS if owner == table}},
            checks,
        )
        for table, (fks, indexes, checks) in before.items()
    }
    _verify(connection, expected, upgrade=False)
