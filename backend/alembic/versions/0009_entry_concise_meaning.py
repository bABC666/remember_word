"""Add the short, human-confirmed study meaning and its decision history.

Revision ID: 0008_public_lexicon_import -> 0009_entry_concise_meaning

Why this exists
---------------
The study page shows one to three short, common, simplified senses per word, and the
owner's product rule for this round explicitly allows those to cover *fewer* senses
than the source lists. That is a different thing from the source's own text, and it
needs somewhere to live that cannot be confused with it:

* ``lexicon_entry.source_raw`` stays the primary source's own line, verbatim;
* ``lexicon_entry.source_meanings`` stays the adjudicated *source default*;
* ``entry_source_evidence.raw_text`` stays the verbatim evidence of what a source
  said, append-only;
* ``entry_concise_meaning`` (this revision) carries the short display value, and says
  whether that wording is the source's own (``source``), a documented change to it
  (``derived``) or a supplement no source contains (``ai_supplement``).

This revision creates tables only. It alters no existing table, backfills nothing and
touches no ``lexicon_entry``, ``word``, ``user_word_state`` or ``review_event`` row.

Relation to the unpublished ``0008``
------------------------------------
``0008_public_lexicon_import`` is **not released and not applied anywhere**: as of
this revision the repository's own records put production at ``0007`` and every
Phase 2.9 rehearsal ran against a synthetic or cloned database. ``0009`` deliberately
stacks on top of it rather than editing it in place:

* ``0009`` links ``source_evidence_id`` to ``entry_source_evidence``, so it cannot
  express anything meaningful on a schema without ``0008``; ``down_revision`` is
  therefore ``0008_public_lexicon_import`` and the pair ships together or not at all;
* ``0008`` was already strengthened in place once (the provenance CHECK constraint,
  design section 9.5). Editing it a second time would silently change a revision that
  another unpublished branch has already rehearsed and described; adding ``0009``
  leaves ``0008`` byte-identical and makes the dependency explicit in the chain.

If ``0008``'s number or content changes before either is released, ``0009``'s
``down_revision`` must follow it. The behaviour this revision adds is additive: a
database that stops at ``0008`` simply shows no concise meanings, and the read path
falls back to the source meanings, which is the designed fallback.

Delete semantics
----------------
Following migration 0007's rule that provenance outlives what it describes:

* ``entry_concise_meaning`` cascades with its entry. The display value is *content*
  of the entry; there is nothing left to show once the entry is gone.
* ``entry_concise_meaning_revision`` is ``ON DELETE SET NULL`` on its entry and
  ``normalized_word`` keeps the word identity, so the record of who decided what the
  shared lexicon shows survives the entry's deletion.
* ``confirmed_by_user_id`` and ``actor_user_id`` are ``ON DELETE SET NULL`` with an
  immutable username snapshot, so removing an account cannot erase who approved a
  displayed meaning.

What the constraints buy
------------------------
* ``length(text) <= 40`` and ``display_order between 1 and 3`` are the product rule
  ("one to three short senses") expressed where a later code path cannot widen it
  without a migration.
* one partial unique index per slot, excluding rejected rows: withdrawing a displayed
  value frees its slot without deleting the record of what was withdrawn.
* ``status <> 'confirmed' OR (confirmed_at IS NOT NULL AND confirmed_by_username <> '')``
  makes an unattributed displayed meaning impossible, and the read path filters on
  ``status = 'confirmed'``, so an unconfirmed candidate cannot reach a page.
* ``provenance_kind = 'ai_supplement' OR source_locator <> ''`` makes a quoted value
  carry its source position, and ``provenance_kind = 'source' OR derivation_note <> ''``
  makes every non-verbatim value say what was changed about it -- a machine-written
  supplement cannot be stored in a shape that reads like a quotation.
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_entry_concise_meaning"
down_revision = "0008_public_lexicon_import"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entry_concise_meaning",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lexicon_entry_id", sa.Integer(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=200), nullable=False),
        sa.Column("provenance_kind", sa.String(length=16), nullable=False),
        sa.Column("source_evidence_id", sa.Integer(), nullable=True),
        sa.Column("source_locator", sa.String(length=200), nullable=False,
                  server_default=""),
        sa.Column("derivation_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="candidate"),
        sa.Column("proposed_by_username", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_by_username", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["lexicon_entry_id"], ["lexicon_entry.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_evidence_id"], ["entry_source_evidence.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_user_id"], ["user.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "length(trim(text)) > 0", name="ck_entry_concise_meaning_text_present"
        ),
        sa.CheckConstraint(
            "length(text) <= 40", name="ck_entry_concise_meaning_text_short"
        ),
        sa.CheckConstraint(
            "display_order between 1 and 3",
            name="ck_entry_concise_meaning_order_range",
        ),
        sa.CheckConstraint(
            "provenance_kind in ('source', 'derived', 'ai_supplement')",
            name="ck_entry_concise_meaning_kind",
        ),
        sa.CheckConstraint(
            "status in ('candidate', 'confirmed', 'rejected')",
            name="ck_entry_concise_meaning_status",
        ),
        # A supplement points at no source; a quoted or derived value must point at one.
        sa.CheckConstraint(
            "provenance_kind = 'ai_supplement' OR length(trim(source_locator)) > 0",
            name="ck_entry_concise_meaning_locator_for_source",
        ),
        # ...and a supplement may not carry a source pointer at all, so "a machine
        # wrote this and a human approved it" cannot be stored in the shape of a
        # quotation.
        sa.CheckConstraint(
            "provenance_kind <> 'ai_supplement'"
            " OR (length(trim(source_locator)) = 0 AND source_evidence_id IS NULL)",
            name="ck_entry_concise_meaning_supplement_has_no_source",
        ),
        # Anything that is not the source's own wording has to say what was changed.
        sa.CheckConstraint(
            "provenance_kind = 'source' OR length(trim(derivation_note)) > 0",
            name="ck_entry_concise_meaning_note_when_not_verbatim",
        ),
        # Nothing is displayed without a named human behind it.
        sa.CheckConstraint(
            "status <> 'confirmed'"
            " OR (confirmed_at IS NOT NULL AND length(trim(confirmed_by_username)) > 0)",
            name="ck_entry_concise_meaning_confirmed_is_attributed",
        ),
    )
    op.create_index("ix_entry_concise_meaning_lexicon_entry_id",
                    "entry_concise_meaning", ["lexicon_entry_id"])
    op.create_index("ix_entry_concise_meaning_source_evidence_id",
                    "entry_concise_meaning", ["source_evidence_id"])
    op.create_index("ix_entry_concise_meaning_status", "entry_concise_meaning", ["status"])
    # One live row per display slot. ``status <> 'rejected'`` is what lets a withdrawn
    # value keep its row while its slot becomes usable again.
    op.create_index(
        "uq_entry_concise_meaning_slot",
        "entry_concise_meaning",
        ["lexicon_entry_id", "display_order"],
        unique=True,
        sqlite_where=sa.text("status <> 'rejected'"),
    )

    op.create_table(
        "entry_concise_meaning_revision",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lexicon_entry_id", sa.Integer(), nullable=True),
        sa.Column("normalized_word", sa.String(length=160), nullable=False),
        sa.Column("concise_meaning_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("provenance_kind", sa.String(length=16), nullable=False,
                  server_default=""),
        sa.Column("source_evidence_id", sa.Integer(), nullable=True),
        sa.Column("source_locator", sa.String(length=200), nullable=False,
                  server_default=""),
        sa.Column("derivation_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_username", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["lexicon_entry_id"], ["lexicon_entry.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["concise_meaning_id"], ["entry_concise_meaning.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_evidence_id"], ["entry_source_evidence.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["user.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_entry_concise_meaning_revision_lexicon_entry_id",
                    "entry_concise_meaning_revision", ["lexicon_entry_id"])
    op.create_index("ix_entry_concise_meaning_revision_normalized_word",
                    "entry_concise_meaning_revision", ["normalized_word"])
    op.create_index("ix_entry_concise_meaning_revision_concise_meaning_id",
                    "entry_concise_meaning_revision", ["concise_meaning_id"])
    op.create_index("ix_entry_concise_meaning_revision_action",
                    "entry_concise_meaning_revision", ["action"])


def downgrade() -> None:
    """Drop the two tables this revision added.

    Nothing pre-existing is touched: ``lexicon_entry``, ``source_meanings``,
    ``source_raw`` and ``entry_source_evidence`` are unaffected, so a downgrade loses
    only the short display values and the record of who confirmed them. Only ever run
    this against a throwaway copy -- the production rules forbid a downgrade.
    """
    op.drop_index("ix_entry_concise_meaning_revision_action",
                  table_name="entry_concise_meaning_revision")
    op.drop_index("ix_entry_concise_meaning_revision_concise_meaning_id",
                  table_name="entry_concise_meaning_revision")
    op.drop_index("ix_entry_concise_meaning_revision_normalized_word",
                  table_name="entry_concise_meaning_revision")
    op.drop_index("ix_entry_concise_meaning_revision_lexicon_entry_id",
                  table_name="entry_concise_meaning_revision")
    op.drop_table("entry_concise_meaning_revision")

    op.drop_index("uq_entry_concise_meaning_slot", table_name="entry_concise_meaning")
    op.drop_index("ix_entry_concise_meaning_status", table_name="entry_concise_meaning")
    op.drop_index("ix_entry_concise_meaning_source_evidence_id",
                  table_name="entry_concise_meaning")
    op.drop_index("ix_entry_concise_meaning_lexicon_entry_id",
                  table_name="entry_concise_meaning")
    op.drop_table("entry_concise_meaning")
