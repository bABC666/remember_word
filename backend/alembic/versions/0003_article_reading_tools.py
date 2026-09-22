"""Persist article translations and word lookup history.

Revision ID: 0003_article_reading_tools
Revises: 0002_reading_assistance
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_article_reading_tools"
down_revision = "0002_reading_assistance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("article") as batch_op:
        batch_op.add_column(sa.Column("translation", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("translated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("translation_ai_raw_json", sa.JSON(), nullable=False, server_default="{}")
        )
    op.create_table(
        "article_word_lookup",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("article.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("surface", sa.String(length=160), nullable=False),
        sa.Column("normalized_word", sa.String(length=160), nullable=False),
        sa.Column("phonetic", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("part_of_speech", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("meaning", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
        sa.Column("context", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=24), nullable=False, server_default="ai"),
        sa.Column(
            "added_word_id",
            sa.Integer(),
            sa.ForeignKey("word.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ai_raw_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("article_id", "normalized_word", name="uq_article_lookup_word"),
    )
    op.create_index("ix_article_word_lookup_article_id", "article_word_lookup", ["article_id"])
    op.create_index(
        "ix_article_word_lookup_normalized_word", "article_word_lookup", ["normalized_word"]
    )
    op.create_index(
        "ix_article_word_lookup_added_word_id", "article_word_lookup", ["added_word_id"]
    )
    op.create_index("ix_article_word_lookup_created_at", "article_word_lookup", ["created_at"])


def downgrade() -> None:
    op.drop_table("article_word_lookup")
    with op.batch_alter_table("article") as batch_op:
        batch_op.drop_column("translation_ai_raw_json")
        batch_op.drop_column("translated_at")
        batch_op.drop_column("translation")
