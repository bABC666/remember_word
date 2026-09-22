"""Initial durable vocabulary schema.

Revision ID: 0001_initial
Revises:

This migration must describe the schema that existed at 2026-09-21 explicitly.
It deliberately does NOT call ``Base.metadata.create_all``: a migration that
reads the live models creates whatever the models look like *today*, which makes
the history unreproducible and lets runtime model edits silently pre-create
columns before later migrations try to add them.
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "word",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("word", sa.String(length=160), nullable=False),
        sa.Column("phonetic", sa.String(length=200), nullable=False),
        sa.Column("part_of_speech", sa.String(length=80), nullable=False),
        sa.Column("source_meanings", sa.JSON(), nullable=False),
        sa.Column("source_raw", sa.Text(), nullable=False),
        sa.Column("anchor", sa.String(length=300), nullable=False),
        sa.Column("semantic_note", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_review", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recall_success", sa.Integer(), nullable=False),
        sa.Column("recall_fail", sa.Integer(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("context_exposure", sa.Integer(), nullable=False),
        sa.Column("possible_issue", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_word_word", "word", ["word"])
    op.create_index("ix_word_status", "word", ["status"])
    op.create_index("ix_word_first_seen", "word", ["first_seen"])
    op.create_index("ix_word_last_review", "word", ["last_review"])
    op.create_index("ix_word_next_review_at", "word", ["next_review_at"])

    op.create_table(
        "import_batch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("raw_ocr_text", sa.Text(), nullable=False),
        sa.Column("raw_ocr_json", sa.JSON(), nullable=False),
        sa.Column("error_stage", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_import_batch_status", "import_batch", ["status"])

    op.create_table(
        "import_image",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("import_batch.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_name", sa.String(length=300), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("ocr_raw_json", sa.JSON(), nullable=False),
        sa.Column("ocr_text", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_import_image_batch_id", "import_image", ["batch_id"])
    op.create_index("ix_import_image_sha256", "import_image", ["sha256"])

    op.create_table(
        "import_candidate",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("import_batch.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("word_id", sa.Integer(), sa.ForeignKey("word.id"), nullable=True),
        sa.Column("word", sa.String(length=160), nullable=False),
        sa.Column("phonetic", sa.String(length=200), nullable=False),
        sa.Column("part_of_speech", sa.String(length=80), nullable=False),
        sa.Column("source_meanings", sa.JSON(), nullable=False),
        sa.Column("source_raw", sa.Text(), nullable=False),
        sa.Column("anchor", sa.String(length=300), nullable=False),
        sa.Column("semantic_note", sa.Text(), nullable=False),
        sa.Column("possible_issue", sa.Boolean(), nullable=False),
        sa.Column("issue_note", sa.Text(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("ai_raw_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_import_candidate_batch_id", "import_candidate", ["batch_id"])
    op.create_index("ix_import_candidate_confirmed", "import_candidate", ["confirmed"])

    op.create_table(
        "article",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_words", sa.JSON(), nullable=False),
        sa.Column("actual_used_words", sa.JSON(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ai_raw_json", sa.JSON(), nullable=False),
    )
    op.create_index("ix_article_created_at", "article", ["created_at"])

    op.create_table(
        "article_word_exposure",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("article.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "word_id",
            sa.Integer(),
            sa.ForeignKey("word.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("exposure_count", sa.Integer(), nullable=False),
        sa.Column("first_exposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_exposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("article_id", "word_id", name="uq_article_word"),
    )
    op.create_index(
        "ix_article_word_exposure_article_id", "article_word_exposure", ["article_id"]
    )
    op.create_index("ix_article_word_exposure_word_id", "article_word_exposure", ["word_id"])

    op.create_table(
        "review_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "word_id",
            sa.Integer(),
            sa.ForeignKey("word.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("article_id", sa.Integer(), sa.ForeignKey("article.id"), nullable=True),
        sa.Column("status_before", sa.String(length=24), nullable=False),
        sa.Column("status_after", sa.String(length=24), nullable=False),
        sa.Column("review_type", sa.String(length=32), nullable=False),
    )
    op.create_index("ix_review_event_word_id", "review_event", ["word_id"])
    op.create_index("ix_review_event_timestamp", "review_event", ["timestamp"])
    op.create_index("ix_review_event_article_id", "review_event", ["article_id"])

    op.create_table(
        "history_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_history_event_event_type", "history_event", ["event_type"])
    op.create_index("ix_history_event_timestamp", "history_event", ["timestamp"])

    op.create_table(
        "app_setting",
        sa.Column("key", sa.String(length=100), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_setting")
    op.drop_table("history_event")
    op.drop_table("review_event")
    op.drop_table("article_word_exposure")
    op.drop_table("article")
    op.drop_table("import_candidate")
    op.drop_table("import_image")
    op.drop_table("import_batch")
    op.drop_table("word")
