"""Remember each user's explicit study lexicon choice.

Revision ID: 0014_selected_lexicon
Revises: 0013_concise_meaning_wikitext_binding
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_selected_lexicon"
down_revision = "0013_concise_meaning_wikitext_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_settings") as batch:
        batch.add_column(
            sa.Column("selected_lexicon_id", sa.Integer(), nullable=True),
        )
        batch.create_foreign_key(
            "fk_user_settings_selected_lexicon", "lexicon",
            ["selected_lexicon_id"], ["id"], ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("user_settings") as batch:
        batch.drop_column("selected_lexicon_id")
