"""Keep session IDs distinct after cleanup deletes old rows.

Revision ID: 0015_session_autoincrement
Revises: 0014_selected_lexicon
"""

from __future__ import annotations

from alembic import op

revision = "0015_session_autoincrement"
down_revision = "0014_selected_lexicon"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite cannot add AUTOINCREMENT to an existing primary key in place.
    # Alembic copies every row, then recreates the existing named indexes.
    with op.batch_alter_table(
        "user_session",
        recreate="always",
        table_kwargs={"sqlite_autoincrement": True},
    ):
        pass


def downgrade() -> None:
    raise RuntimeError(
        "0015 downgrade refused: removing AUTOINCREMENT can reuse deleted "
        "session IDs; restore a verified pre-0015 backup with matching code instead"
    )
