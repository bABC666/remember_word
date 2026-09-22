"""Let an article exposure reference a lexicon entry.

Revision ID: 0005 -> 0006_article_exposure_entry

Phase 0 specified ``article_word_exposure.lexicon_entry_id`` as a nullable
bridge, and V1.2 needs it: a word a *new* user adds from an article has no legacy
``word`` row, so ``word_id`` alone cannot identify it.

``word_id`` therefore becomes nullable and the uniqueness rule moves to a pair of
partial indexes keyed on the lexicon entry. The legacy ``UNIQUE(article_id,
word_id)`` becomes a partial index with the same semantics (SQLite treats NULLs
as distinct anyway), so existing rows keep exactly the constraint they had.

Additive and reversible: no data is deleted, and downgrade restores the original
shape.
"""

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa

revision = "0006_article_exposure_entry"
down_revision = "0005_lexicon_and_migration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")

    # Review events need the same treatment: a word a new user adds from reading
    # has no legacy word row, so ``word_id`` must be nullable and the lexicon
    # entry recorded instead.
    with op.batch_alter_table("review_event") as batch:
        batch.add_column(sa.Column("lexicon_entry_id", sa.Integer(), nullable=True))
        batch.alter_column("word_id", existing_type=sa.Integer(), nullable=True)
    op.create_index(
        "ix_review_event_lexicon_entry_id", "review_event", ["lexicon_entry_id"]
    )
    connection.execute(
        sa.text(
            "update review_event set lexicon_entry_id = "
            "(select lexicon_entry_id from word where word.id = review_event.word_id) "
            "where lexicon_entry_id is null and word_id is not null"
        )
    )

    with op.batch_alter_table("article_word_exposure") as batch:
        batch.add_column(sa.Column("lexicon_entry_id", sa.Integer(), nullable=True))
        # V1.2 words added from reading have no legacy word row.
        batch.alter_column("word_id", existing_type=sa.Integer(), nullable=True)
        batch.drop_constraint("uq_article_word", type_="unique")
    op.create_index(
        "ix_article_word_exposure_lexicon_entry_id",
        "article_word_exposure",
        ["lexicon_entry_id"],
    )
    # Partial unique indexes keep one exposure per word per article while
    # allowing rows that only carry a lexicon entry.
    op.execute(
        "create unique index uq_article_word on article_word_exposure(article_id, word_id) "
        "where word_id is not null"
    )
    op.execute(
        "create unique index uq_article_entry on article_word_exposure("
        "article_id, lexicon_entry_id) where lexicon_entry_id is not null"
    )

    # Backfill: existing exposures point at migrated words, so their entry is known.
    connection.execute(
        sa.text(
            "update article_word_exposure set lexicon_entry_id = "
            "(select lexicon_entry_id from word where word.id = article_word_exposure.word_id) "
            "where lexicon_entry_id is null and word_id is not null"
        )
    )
    connection.execute(
        sa.text(
            "insert into history_event (user_id, event_type, timestamp, entity_type, "
            "entity_id, payload) values (null, 'exposure_entry_backfill', :now, "
            "'database', null, :payload)"
        ),
        {
            "now": now,
            "payload": '{"migration": "0006_article_exposure_entry"}',
        },
    )


def downgrade() -> None:
    # Rows that only carry a lexicon entry cannot be represented in the old
    # shape; they are removed explicitly and reported rather than silently lost.
    connection = op.get_bind()
    orphaned = connection.execute(
        sa.text(
            "select count(*) from article_word_exposure where word_id is null"
        )
    ).scalar_one()
    if orphaned:
        connection.execute(
            sa.text("delete from article_word_exposure where word_id is null")
        )
        connection.execute(
            sa.text(
                "insert into history_event (user_id, event_type, timestamp, entity_type, "
                "entity_id, payload) values (null, 'exposure_entry_downgrade_dropped', "
                ":now, 'database', null, :payload)"
            ),
            {
                "now": datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" "),
                "payload": f'{{"dropped_rows": {orphaned}}}',
            },
        )

    op.execute("drop index if exists uq_article_entry")
    op.execute("drop index if exists uq_article_word")
    op.drop_index(
        "ix_article_word_exposure_lexicon_entry_id", table_name="article_word_exposure"
    )
    with op.batch_alter_table("article_word_exposure") as batch:
        batch.alter_column("word_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_column("lexicon_entry_id")
        batch.create_unique_constraint(
            "uq_article_word", ["article_id", "word_id"]
        )

    # Review events: the same rows cannot be represented without a legacy word.
    review_orphans = connection.execute(
        sa.text("select count(*) from review_event where word_id is null")
    ).scalar_one()
    if review_orphans:
        connection.execute(sa.text("delete from review_event where word_id is null"))
        connection.execute(
            sa.text(
                "insert into history_event (user_id, event_type, timestamp, entity_type, "
                "entity_id, payload) values (null, 'review_event_downgrade_dropped', "
                ":now, 'database', null, :payload)"
            ),
            {
                "now": datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" "),
                "payload": f'{{"dropped_rows": {review_orphans}}}',
            },
        )
    op.drop_index("ix_review_event_lexicon_entry_id", table_name="review_event")
    with op.batch_alter_table("review_event") as batch:
        batch.alter_column("word_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_column("lexicon_entry_id")
