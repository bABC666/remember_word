"""Record public lexicon import provenance and confirmation runs.

Revision ID: 0007_bridge_foreign_keys -> 0008_public_lexicon_import

Why this exists
---------------
Phase 2.9 imports a shared public lexicon from external source files. The design
(``docs/V1.2-PHASE2.9-CONFIRM-WRITE-DESIGN.md``) requires that every displayed
default value can be traced back to the file, line and field it came from, that a
human's decisions are recorded rather than inferred, and that confirming the same
plan twice writes nothing the second time.

``lexicon_entry`` keeps carrying the *selected default snapshot* in its existing
columns. It cannot express several sources disagreeing about one word: a single
``source_meanings``/``source_raw``/``phonetic`` set would force one source to
overwrite another. The evidence therefore lives beside it in a new table, and the
existing columns are not altered or backfilled by this migration.

Numbering
---------
This revision took the next free number at implementation time rather than a
number reserved in prose. ``revoke_reason`` and Phase 5's adaptive-scheduling
column were both *expected* to land at ``0008`` in earlier documents, but neither
is implemented and neither had a locked number; they take whatever is next free
when they are actually built. The chain stays linear and single-headed, which
``backend/tests/test_migrations.py`` and ``test_revision_guard.py`` enforce.

Delete semantics
----------------
Foreign keys follow migration 0007's rule -- provenance must outlive the rows it
describes:

* ``entry_source_evidence.lexicon_entry_id`` is ``ON DELETE SET NULL``, not
  ``CASCADE``. Evidence records what a source said; deleting an entry must not
  erase that, and ``normalized_word`` keeps the word identity.
* ``public_import_run.confirmed_by_user_id`` is ``ON DELETE SET NULL``, with
  ``confirmed_by_username`` kept as an immutable snapshot, so deleting an account
  cannot silently erase who confirmed a public import.
* ``target_lexicon_id`` and the artifact references are ``ON DELETE RESTRICT``:
  a lexicon or artifact that a run depends on cannot be removed out from under it.

Also note what the unique keys buy:

* ``source_artifact (file_sha256, mapping_sha256, role)`` -- the same bytes read
  through the same mapping are one artifact however the manifest labels them.
* ``public_import_run.plan_sha256`` -- the retry key. Re-confirming a plan returns
  the original run instead of writing again.
* ``entry_source_evidence (evidence_sha256, import_run_id)`` -- within one run a
  piece of evidence is recorded once; across runs a re-adjudication appends its own
  row. Keying on ``evidence_sha256`` alone would make re-adjudication either
  impossible or a rewrite of history.

Authorisation metadata is mandatory
-----------------------------------
``source_artifact`` carries a CHECK constraint requiring the publisher, version,
acquisition time, licence id, use scope and display scope to be non-empty. The
design's rule is that a source whose content licence is unsettled may be evaluated
locally but must not be published, and the failure mode it guards against is an
import that *looks* complete: rows on disk, evidence traceable to a file, and a
blank licence nobody notices until the question is asked. The plan blocks on
incomplete provenance and the confirmation refuses it with a readable message;
this constraint is what makes a blank row impossible rather than merely discouraged.
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_public_lexicon_import"
down_revision = "0007_bridge_foreign_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_artifact",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("publisher", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("version", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("obtained_at_utc", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("format", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("mapping_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("mapping_sha256", sa.String(length=64), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("license_id", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("license_text_sha256", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("use_scope", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("display_scope", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("storage_locator", sa.String(length=400), nullable=False,
                  server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "file_sha256", "mapping_sha256", "role", name="uq_source_artifact_identity"
        ),
        # No blank authorisation metadata, enforced by the database rather than only
        # by the application: a row with an empty publisher, version, acquisition
        # time, licence or scope is an import nobody can audit later.
        sa.CheckConstraint(
            "length(trim(publisher)) > 0"
            " AND length(trim(version)) > 0"
            " AND length(trim(obtained_at_utc)) > 0"
            " AND length(trim(license_id)) > 0"
            " AND length(trim(use_scope)) > 0"
            " AND length(trim(display_scope)) > 0",
            name="ck_source_artifact_provenance_present",
        ),
    )
    op.create_index("ix_source_artifact_mapping_sha256", "source_artifact",
                    ["mapping_sha256"])
    op.create_index("ix_source_artifact_file_sha256", "source_artifact", ["file_sha256"])

    op.create_table(
        "public_import_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_sha256", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("target_lexicon_id", sa.Integer(), nullable=False),
        sa.Column("confirmed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_by_username", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("entries_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entries_matched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error_report_locator", sa.String(length=400), nullable=False,
                  server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["target_lexicon_id"], ["lexicon.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_user_id"], ["user.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("plan_sha256", name="uq_public_import_run_plan"),
    )
    op.create_index("ix_public_import_run_target_lexicon_id", "public_import_run",
                    ["target_lexicon_id"])

    op.create_table(
        "public_import_run_source",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("import_run_id", sa.Integer(), nullable=False),
        sa.Column("source_artifact_id", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["import_run_id"], ["public_import_run.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_artifact_id"], ["source_artifact.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "import_run_id", "source_artifact_id", name="uq_public_import_run_source"
        ),
    )
    op.create_index("ix_public_import_run_source_import_run_id",
                    "public_import_run_source", ["import_run_id"])
    op.create_index("ix_public_import_run_source_source_artifact_id",
                    "public_import_run_source", ["source_artifact_id"])

    op.create_table(
        "entry_source_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lexicon_entry_id", sa.Integer(), nullable=True),
        sa.Column("source_artifact_id", sa.Integer(), nullable=False),
        sa.Column("import_run_id", sa.Integer(), nullable=False),
        sa.Column("normalized_word", sa.String(length=160), nullable=False),
        sa.Column("row_locator", sa.Integer(), nullable=False),
        sa.Column("field_kind", sa.String(length=24), nullable=False),
        sa.Column("sense_key", sa.String(length=80), nullable=False),
        sa.Column("raw_word", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("raw_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("selected_for_default", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("selection_order", sa.Integer(), nullable=True),
        sa.Column("confirmed_by_username", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["lexicon_entry_id"], ["lexicon_entry.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_artifact_id"], ["source_artifact.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["import_run_id"], ["public_import_run.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "evidence_sha256", "import_run_id", name="uq_entry_source_evidence"
        ),
    )
    op.create_index("ix_entry_source_evidence_lexicon_entry_id",
                    "entry_source_evidence", ["lexicon_entry_id"])
    op.create_index("ix_entry_source_evidence_import_run_id",
                    "entry_source_evidence", ["import_run_id"])
    op.create_index("ix_entry_source_evidence_source_artifact_id",
                    "entry_source_evidence", ["source_artifact_id"])
    op.create_index("ix_entry_source_evidence_normalized_word",
                    "entry_source_evidence", ["normalized_word"])


def downgrade() -> None:
    """Drop the four tables this revision added.

    Everything here was created by this revision, so the downgrade is exact: no
    pre-existing table or column is touched, and no row of ``lexicon_entry``,
    ``word``, ``user_word_state`` or ``review_event`` is affected. Only ever run
    this against a throwaway copy -- the production rules forbid a downgrade.
    """
    op.drop_index("ix_entry_source_evidence_normalized_word",
                  table_name="entry_source_evidence")
    op.drop_index("ix_entry_source_evidence_source_artifact_id",
                  table_name="entry_source_evidence")
    op.drop_index("ix_entry_source_evidence_import_run_id",
                  table_name="entry_source_evidence")
    op.drop_index("ix_entry_source_evidence_lexicon_entry_id",
                  table_name="entry_source_evidence")
    op.drop_table("entry_source_evidence")

    op.drop_index("ix_public_import_run_source_source_artifact_id",
                  table_name="public_import_run_source")
    op.drop_index("ix_public_import_run_source_import_run_id",
                  table_name="public_import_run_source")
    op.drop_table("public_import_run_source")

    op.drop_index("ix_public_import_run_target_lexicon_id",
                  table_name="public_import_run")
    op.drop_table("public_import_run")

    op.drop_index("ix_source_artifact_file_sha256", table_name="source_artifact")
    op.drop_index("ix_source_artifact_mapping_sha256", table_name="source_artifact")
    op.drop_table("source_artifact")
