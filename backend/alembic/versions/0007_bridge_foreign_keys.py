"""Give the V1.2 bridge columns the foreign keys the ORM claims they have.

Revision ID: 0006_article_exposure_entry -> 0007_bridge_foreign_keys

Why this exists
---------------
Migrations 0004 and 0005 added their bridge columns with
``batch_alter_table(...).add_column(sa.Column(..., sa.ForeignKey(...)))``. SQLite
performs a plain ``ALTER TABLE ADD COLUMN`` for a nullable column, and SQLite
cannot attach a foreign key that way: the constraint is silently dropped. The ORM
therefore declared 26 foreign keys while the physical schema only had 17, and
``PRAGMA foreign_key_check`` could not reveal the gap because a constraint that
was never created is never violated.

Nine columns were affected::

    word.user_id                        import_batch.user_id
    word.lexicon_entry_id               import_candidate.lexicon_entry_id
    article.user_id                     review_event.user_id
    article_word_exposure.lexicon_entry_id   review_event.lexicon_entry_id
    history_event.user_id

They are added here by rebuilding the affected tables, which is the only way
SQLite can gain a foreign key.

Delete semantics
----------------
Every affected column is nullable, and the whole point of these bridges is to
preserve history. ``ON DELETE SET NULL`` is used throughout so that removing a
user, a lexicon entry or a word can never delete the learning history attached to
it -- a review, an article or an exposure survives with its bridge cleared. This
matches the ORM, which already declares SET NULL for all nine.
"""

from alembic import op

revision = "0007_bridge_foreign_keys"
down_revision = "0006_article_exposure_entry"
branch_labels = None
depends_on = None

#: table -> (column, referenced table, ondelete) for every bridge column that
#: migration 0004/0005 added without a physical foreign key.
BRIDGES: tuple[tuple[str, str, str, str], ...] = (
    ("word", "user_id", "user", "SET NULL"),
    ("word", "lexicon_entry_id", "lexicon_entry", "SET NULL"),
    ("review_event", "user_id", "user", "SET NULL"),
    ("review_event", "lexicon_entry_id", "lexicon_entry", "SET NULL"),
    ("article", "user_id", "user", "SET NULL"),
    ("article_word_exposure", "lexicon_entry_id", "lexicon_entry", "SET NULL"),
    ("import_batch", "user_id", "user", "SET NULL"),
    ("import_candidate", "lexicon_entry_id", "lexicon_entry", "SET NULL"),
    ("history_event", "user_id", "user", "SET NULL"),
)


def _constraint_name(table: str, column: str) -> str:
    return f"fk_{table}_{column}"


def _foreign_key_enforcement_is_off(connection) -> bool:
    """True when the migration connection is not enforcing foreign keys.

    A table rebuild requires enforcement to be off, and the pragma cannot be
    changed from inside a migration: ``PRAGMA foreign_keys`` is a silent no-op
    while a transaction is open, so a migration that "turns it off" may still be
    running with it on. ``alembic/env.py`` sets it on the raw DBAPI connection
    instead, before any statement runs. This only *verifies* that.
    """
    return not connection.exec_driver_sql("PRAGMA foreign_keys").scalar()


def _require_enforcement_off(connection, *, direction: str) -> None:
    if _foreign_key_enforcement_is_off(connection):
        return
    raise RuntimeError(
        f"无法在 {direction} 中重建表：当前连接启用了 foreign_keys。\n"
        "SQLite 只能在重建表时增删外键，而重建过程会 drop 旧表；"
        "在外键启用状态下 drop 会触发子表的 ON DELETE 动作，造成静默数据丢失。\n"
        "PRAGMA foreign_keys 在事务内是空操作，因此必须由 alembic/env.py "
        "在 DBAPI 连接建立时关闭（见 _disable_foreign_key_enforcement）。"
    )


def upgrade() -> None:
    # The pragma is never toggled here: it is set once, on the raw connection, by
    # alembic/env.py. What this migration does instead is refuse to run if
    # enforcement is somehow on, then prove the data is still consistent.
    connection = op.get_bind()
    _require_enforcement_off(connection, direction="upgrade")

    for table, column, parent, ondelete in BRIDGES:
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.create_foreign_key(
                _constraint_name(table, column),
                parent,
                [column],
                ["id"],
                ondelete=ondelete,
            )

    # Fail loudly rather than leaving a database whose constraints are missing.
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(
            "0007 完成后外键校验失败，迁移数据可能不一致："
            f"{violations[:10]}"
        )
    _require_enforcement_off(connection, direction="upgrade")


def downgrade() -> None:
    """Remove the constraints again, restoring the pre-0007 shape.

    Guarded by ``alembic/env.py``: this can only run against a staging clone or a
    temporary database, never the live one. Removing a foreign key requires the
    same table rebuild that added it.
    """
    connection = op.get_bind()
    _require_enforcement_off(connection, direction="downgrade")

    for table, column, _parent, _ondelete in BRIDGES:
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.drop_constraint(_constraint_name(table, column), type_="foreignkey")


__all__ = ["BRIDGES", "down_revision", "downgrade", "revision", "upgrade"]
