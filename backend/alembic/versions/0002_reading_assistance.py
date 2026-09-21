"""Import recovery and reading assistance schema.

Revision ID: 0002_reading_assistance
Revises: 0001_initial
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_reading_assistance"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("import_batch") as batch_op:
        batch_op.add_column(
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_index("ix_import_batch_is_deleted", ["is_deleted"])
    with op.batch_alter_table("import_image") as batch_op:
        batch_op.add_column(
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_index("ix_import_image_is_deleted", ["is_deleted"])


def downgrade() -> None:
    with op.batch_alter_table("import_image") as batch_op:
        batch_op.drop_index("ix_import_image_is_deleted")
        batch_op.drop_column("is_deleted")
    with op.batch_alter_table("import_batch") as batch_op:
        batch_op.drop_index("ix_import_batch_is_deleted")
        batch_op.drop_column("is_deleted")
