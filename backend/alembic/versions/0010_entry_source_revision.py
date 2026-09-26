"""Give every source-evidence row the pinned revision it was read from.

Revision ID: 0009_entry_concise_meaning -> 0010_entry_source_revision

Why this exists
---------------
``entry_source_evidence`` records the *physical line* a value came from
(``row_locator``) but not the *revision* that line belonged to. For a source whose
pages are revised independently -- zh.wiktionary pins one ``oldid`` per word -- the
line alone cannot answer "which version of that page did we use", so a reader could
not get back to the exact revision a displayed meaning was taken from, and a later
re-fetch of the same page could silently show different text.

The revision cannot ride along in the mapping's ``columns``: the preview refuses any
column outside the four canonical fields, exactly so that a metadata column cannot
masquerade as lexicon content. It is therefore a separate declaration inside the
frozen mapping, and the value that declaration yields for one row is stored here.

Shape of the change
-------------------
One column plus the constraint that keeps it a value rather than prose:

* ``source_revision`` -- empty when the source declares no revision at all, or when
  this row's cell is empty (a word whose page does not exist upstream). Both are
  legal and both mean "no link", which is why the column is ``NOT NULL DEFAULT ''``
  rather than nullable: there is exactly one way to record "unknown", and it is not
  also the way to record "the row forgot to say".
* ``ck_entry_source_evidence_revision_trimmed`` -- what is stored is already
  trimmed, so a link built from this value cannot differ from the value it claims to
  be by invisible whitespace.

SQLite can neither add nor drop a constraint in place, so this rebuilds the table --
the same operation ``0007_bridge_foreign_keys`` performs, and for the same reason
``alembic/env.py`` turns foreign-key enforcement off on the raw connection before any
statement runs: dropping the old table with enforcement on would fire the children's
ON DELETE actions and turn a rebuild into silent data loss. The rebuild carries the
three foreign keys, the unique key and the indexes over unchanged, and this module
verifies that instead of assuming it.

Boundary of the constraint
--------------------------
SQLite's one-argument ``trim()`` removes spaces only, so a tab or newline at either
end would pass this check. Refusing those belongs to the validation of the mapping
declaration that reads the value, not to the column: the constraint's job is that the
stored revision is comparable byte-for-byte with the one a re-read would produce.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_entry_source_revision"
down_revision = "0009_entry_concise_meaning"
branch_labels = None
depends_on = None

TABLE = "entry_source_evidence"
COLUMN = "source_revision"
CHECK_NAME = "ck_entry_source_evidence_revision_trimmed"

#: column -> (parent table, ON DELETE rule) this table must carry, before and after
#: the rebuild. Read from 0008 rather than from the ORM, because this migration has
#: to prove what the *database* does, not what the models intend.
EXPECTED_FOREIGN_KEYS: tuple[tuple[str, str, str], ...] = (
    ("lexicon_entry_id", "lexicon_entry", "SET NULL"),
    ("source_artifact_id", "source_artifact", "RESTRICT"),
    ("import_run_id", "public_import_run", "RESTRICT"),
)

#: The unique key from 0008, as (columns, unique) -- a rebuild that dropped it would
#: make a re-adjudicated source position indistinguishable from a duplicate.
EXPECTED_UNIQUE_KEY: tuple[tuple[str, ...], bool] = (
    ("evidence_sha256", "import_run_id"),
    True,
)

#: The indexes 0008 created, which a rebuild must not quietly discard.
EXPECTED_INDEXES = frozenset(
    {
        "ix_entry_source_evidence_lexicon_entry_id",
        "ix_entry_source_evidence_import_run_id",
        "ix_entry_source_evidence_source_artifact_id",
        "ix_entry_source_evidence_normalized_word",
    }
)


def _foreign_key_enforcement_is_off(connection) -> bool:
    """True when the migration connection is not enforcing foreign keys.

    A table rebuild requires enforcement to be off, and the pragma cannot be changed
    from inside a migration: ``PRAGMA foreign_keys`` is a silent no-op while a
    transaction is open, so a migration that "turns it off" may still be running with
    it on. ``alembic/env.py`` sets it on the raw DBAPI connection instead, before any
    statement runs. This only *verifies* that -- deliberately duplicated from 0007,
    because each revision file has to stand alone: importing a helper from another
    revision would make this one break when that one is edited or removed.
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


def _verify_rebuilt_table(connection, *, direction: str) -> None:
    """Prove the rebuild kept the constraints 0008 created.

    A missing foreign key is invisible to ``PRAGMA foreign_key_check``: a constraint
    that was never created is never violated. So the physical list is asserted
    directly, in both directions -- the downgrade rebuilds the table too.

    ``PRAGMA foreign_key_list`` columns are ``id|seq|table|from|to|on_update|
    on_delete|match``, so the local column is index 3 (index 1 is ``seq``, which is 0
    for every row of a single-column foreign key -- keying on it would collapse all
    three constraints into one and prove nothing).
    """
    found = {
        row[3]: (row[2], (row[6] or "NO ACTION").upper())
        for row in connection.exec_driver_sql(f'PRAGMA foreign_key_list("{TABLE}")')
    }
    expected = {column: (parent, rule) for column, parent, rule in EXPECTED_FOREIGN_KEYS}
    if found != expected:
        raise RuntimeError(
            f"0010 {direction} 后 {TABLE} 的外键与 0008 不符，重建丢掉了约束："
            f"期望 {sorted(expected.items())}，实际 {sorted(found.items())}。"
        )

    unique_keys = set()
    for row in connection.exec_driver_sql(f'PRAGMA index_list("{TABLE}")'):
        name, is_unique = row[1], bool(row[2])
        if name.startswith("sqlite_autoindex"):
            columns = tuple(
                item[2]
                for item in connection.exec_driver_sql(f'PRAGMA index_info("{name}")')
            )
            unique_keys.add((columns, is_unique))
    if EXPECTED_UNIQUE_KEY not in unique_keys:
        raise RuntimeError(
            f"0010 {direction} 后 {TABLE} 的唯一键丢失（期望 {EXPECTED_UNIQUE_KEY}，"
            f"实际 {sorted(unique_keys)}）：同一来源位置的重新裁定将无法与重复行区分。"
        )

    indexes = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
            (TABLE,),
        )
    }
    missing = EXPECTED_INDEXES - indexes
    if missing:
        raise RuntimeError(
            f"0010 {direction} 后 {TABLE} 的索引丢失：{sorted(missing)}；"
            "重建必须保留 0008 建立的全部索引。"
        )

    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(
            f"0010 {direction} 后外键校验失败，迁移数据可能不一致：{violations[:10]}"
        )
    integrity = connection.exec_driver_sql("PRAGMA integrity_check").scalar()
    if integrity != "ok":
        raise RuntimeError(f"0010 {direction} 后完整性校验失败：{integrity}")


def upgrade() -> None:
    connection = op.get_bind()
    _require_enforcement_off(connection, direction="upgrade")

    # ``recreate="always"`` because a CHECK constraint can only be added by rebuilding
    # the table; letting Alembic decide would leave a path where the column lands and
    # the constraint silently does not.
    with op.batch_alter_table(TABLE, recreate="always") as batch:
        batch.add_column(
            sa.Column(COLUMN, sa.String(length=64), nullable=False, server_default="")
        )
        batch.create_check_constraint(CHECK_NAME, f"{COLUMN} = trim({COLUMN})")

    _verify_rebuilt_table(connection, direction="upgrade")
    _require_enforcement_off(connection, direction="upgrade")


def downgrade() -> None:
    """Remove the column and its constraint, restoring the pre-0010 shape.

    Guarded by ``alembic/env.py``: this can only run against a staging clone or a
    temporary database, never the live one. Removing a CHECK constraint needs the same
    rebuild that added it.
    """
    connection = op.get_bind()
    _require_enforcement_off(connection, direction="downgrade")

    with op.batch_alter_table(TABLE, recreate="always") as batch:
        batch.drop_constraint(CHECK_NAME, type_="check")
        batch.drop_column(COLUMN)

    _verify_rebuilt_table(connection, direction="downgrade")


__all__ = [
    "CHECK_NAME",
    "COLUMN",
    "EXPECTED_FOREIGN_KEYS",
    "TABLE",
    "down_revision",
    "downgrade",
    "revision",
    "upgrade",
]
