"""Add the lexicon model and migrate V1.1 single-user data forward.

Revision ID: 0005_lexicon_and_migration
Revises: 0004_multiuser_foundation

Additive by design. Nothing is dropped, renamed or deleted, and the legacy
``word`` table keeps every original value. Existing rows gain ownership and a
bridge to the new lexicon entries; the new content and state tables carry what
V1.1 stored in a single row.

Word frequency columns are deliberately left NULL: the project has no external
word-frequency dataset, and inventing ranks would violate the data principles.

Ownership model: ``review_event``, ``article``, ``import_batch`` and
``history_event`` carry ``user_id`` because they are the roots of their private
trees. ``article_word_exposure`` and ``article_word_lookup`` deliberately do NOT
duplicate it: they inherit ownership from their article, so a parent and child
can never disagree about who owns the data.
"""

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa

revision = "0005_lexicon_and_migration"
down_revision = "0004_multiuser_foundation"
branch_labels = None
depends_on = None

#: Name of the lexicon the V1.1 word rows are migrated into.
SYSTEM_LEXICON_NAME = "考研核心词汇"
SYSTEM_LEXICON_DESCRIPTION = "由 V1.1 单用户数据迁移生成的系统公共词库"
SOURCE_TYPE = "migrated_v11"

#: Instance-level events that stay unowned: they are not any user's data.
USER_EVENT_TYPES = (
    "import_confirmed",
    "import_batch_removed",
    "import_image_removed",
    "article_word_lookup",
    "article_word_added",
    "article_translated",
    "user_login",
    "user_password_changed",
    "user_created",
    "user_updated",
    "v12_migration",
)


def _now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")


def _add_compat_columns() -> None:
    """Nullable columns only, so SQLite alters in place without a table rebuild."""
    with op.batch_alter_table("word") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("lexicon_entry_id", sa.Integer(), nullable=True))
    op.create_index("ix_word_user_id", "word", ["user_id"])
    op.create_index("ix_word_lexicon_entry_id", "word", ["lexicon_entry_id"])

    with op.batch_alter_table("review_event") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_index("ix_review_event_user_id", "review_event", ["user_id"])

    with op.batch_alter_table("article") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_index("ix_article_user_id", "article", ["user_id"])

    with op.batch_alter_table("import_batch") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_index("ix_import_batch_user_id", "import_batch", ["user_id"])

    with op.batch_alter_table("import_candidate") as batch:
        batch.add_column(sa.Column("lexicon_entry_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_import_candidate_lexicon_entry_id", "import_candidate", ["lexicon_entry_id"]
    )

    with op.batch_alter_table("history_event") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_index("ix_history_event_user_id", "history_event", ["user_id"])


def _create_lexicon_tables() -> None:
    op.create_table(
        "lexicon",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("visibility", sa.String(length=16), nullable=False, server_default="private"),
        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("entry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_lexicon_owner_user_id", "lexicon", ["owner_user_id"])
    # SQLite treats NULLs as distinct in a UNIQUE constraint, so partial indexes
    # are the only way to enforce uniqueness for system (ownerless) lexicons and
    # per-owner lexicon names.
    op.execute(
        "create unique index uq_lexicon_system on lexicon(source_type) "
        "where owner_user_id is null"
    )
    op.execute(
        "create unique index uq_lexicon_user_name on lexicon(owner_user_id, name) "
        "where owner_user_id is not null"
    )

    op.create_table(
        "lexicon_entry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lexicon_id", sa.Integer(), nullable=False),
        sa.Column("word", sa.String(length=160), nullable=False),
        sa.Column("normalized_word", sa.String(length=160), nullable=False),
        sa.Column("phonetic", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("part_of_speech", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("source_meanings", sa.JSON(), nullable=False),
        sa.Column("source_raw", sa.Text(), nullable=False, server_default=""),
        sa.Column("default_anchor", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("semantic_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("possible_issue", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sequence", sa.Integer(), nullable=True),
        sa.Column("frequency_rank", sa.Integer(), nullable=True),
        sa.Column("frequency_count", sa.Integer(), nullable=True),
        sa.Column("frequency_source", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lexicon_id"], ["lexicon.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_lexicon_entry_lexicon_id", "lexicon_entry", ["lexicon_id"])
    op.create_index("ix_lexicon_entry_normalized_word", "lexicon_entry", ["normalized_word"])
    op.create_index("ix_lexicon_entry_sequence", "lexicon_entry", ["sequence"])
    op.create_index(
        "uq_lexicon_entry_word", "lexicon_entry", ["lexicon_id", "normalized_word"], unique=True
    )
    op.create_index(
        "ix_lexicon_entry_frequency", "lexicon_entry", ["lexicon_id", "frequency_rank"]
    )

    op.create_table(
        "user_lexicon",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("lexicon_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("daily_new_words", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lexicon_id"], ["lexicon.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_user_lexicon_user_id", "user_lexicon", ["user_id"])
    op.create_index("ix_user_lexicon_lexicon_id", "user_lexicon", ["lexicon_id"])
    op.create_index("uq_user_lexicon", "user_lexicon", ["user_id", "lexicon_id"], unique=True)

    op.create_table(
        "user_word_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("lexicon_entry_id", sa.Integer(), nullable=False),
        sa.Column("legacy_word_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="new"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_review", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recall_success", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recall_fail", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("context_exposure", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("anchor_override", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("semantic_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("possible_issue", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["lexicon_entry_id"], ["lexicon_entry.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_user_word_state_user_id", "user_word_state", ["user_id"])
    op.create_index(
        "ix_user_word_state_lexicon_entry_id", "user_word_state", ["lexicon_entry_id"]
    )
    op.create_index("ix_user_word_state_legacy_word_id", "user_word_state", ["legacy_word_id"])
    op.create_index(
        "uq_user_word_state", "user_word_state", ["user_id", "lexicon_entry_id"], unique=True
    )
    op.create_index("ix_uws_user_status", "user_word_state", ["user_id", "status"])
    op.create_index("ix_uws_user_due", "user_word_state", ["user_id", "next_review_at"])


def _admin_user_id(connection) -> int | None:
    row = connection.execute(
        sa.text("select id from user where role = 'admin' order by id limit 1")
    ).fetchone()
    return row[0] if row else None


def _normalized_word_key(value: str | None) -> str:
    return (value or "").strip().casefold()


def find_normalized_duplicates(word_rows) -> dict[str, list[tuple[int, str]]]:
    """Group legacy words whose normalized form collides.

    V1.1 never constrained ``word.word`` after ``strip().casefold()``, but the
    V1.2 model keys lexicon entries on exactly that normalized value. Two rows
    that normalize identically would make the second ``UserWordState`` violate
    ``UNIQUE(user_id, lexicon_entry_id)`` and abort the migration halfway.
    """
    grouped: dict[str, list[tuple[int, str]]] = {}
    for row in word_rows:
        grouped.setdefault(_normalized_word_key(row["word"]), []).append(
            (row["id"], row["word"])
        )
    return {key: rows for key, rows in grouped.items() if len(rows) > 1}


def preflight_normalized_words(connection) -> None:
    """Fail loudly before touching any data if legacy words collide.

    Silently merging, dropping or picking a winner would lose a distinct word the
    user reviewed, so this refuses and reports the exact rows for a human to
    resolve. It runs before the first write of the migration.
    """
    word_rows = (
        connection.execute(
            sa.text("select id, word from word order by id")
        )
        .mappings()
        .all()
    )
    duplicates = find_normalized_duplicates(word_rows)
    if not duplicates:
        return

    lines = [
        "迁移中止：legacy word 表中存在规范化后重复的词条。",
        "规范化规则为 strip().casefold()；V1.1 未对该结果做唯一性约束，",
        "而 V1.2 的 LexiconEntry 以该值为键，继续迁移会触发",
        "UNIQUE(user_id, lexicon_entry_id) 冲突并中途失败。",
        "",
        "请人工决定如何处理以下冲突（本迁移不会自动合并、删除或覆盖任何词条）：",
    ]
    for key, rows in sorted(duplicates.items()):
        detail = ", ".join(f"id={row_id} word={value!r}" for row_id, value in rows)
        lines.append(f"  normalized={key!r} -> {detail}")
    lines.append("")
    lines.append("处理方式：在 word 表中改写其中一个词形（保留其 id 与全部历史），")
    lines.append("或先导出再删除重复行；随后重新运行迁移。")
    raise RuntimeError("\n".join(lines))


def _migrate_data() -> None:
    connection = op.get_bind()

    admin_id = _admin_user_id(connection)
    if admin_id is None:
        # No admin means there is nothing to attribute single-user history to;
        # leave the new tables empty rather than inventing an owner.
        return

    now = _now()
    word_rows = (
        connection.execute(
            sa.text(
                "select id, word, phonetic, part_of_speech, source_meanings, source_raw, "
                "anchor, semantic_note, possible_issue, status, first_seen, last_review, "
                "next_review_at, recall_success, recall_fail, consecutive_failures, "
                "context_exposure, notes, created_at, updated_at from word order by id"
            )
        )
        .mappings()
        .all()
    )

    lexicon_id = _ensure_system_lexicon(connection, len(word_rows), now)
    daily_new_words = _int_setting(connection, "daily_new_words", 15)
    _migrate_user_settings(connection, admin_id, daily_new_words, now)
    _ensure_user_lexicon(connection, admin_id, lexicon_id, daily_new_words, now)

    for row in word_rows:
        entry_id = _ensure_entry(connection, lexicon_id, row)
        _ensure_user_word_state(connection, admin_id, entry_id, row)
        connection.execute(
            sa.text("update word set lexicon_entry_id = :entry_id where id = :id"),
            {"entry_id": entry_id, "id": row["id"]},
        )

    _backfill_ownership(connection, admin_id)
    _record_audit(connection, admin_id, lexicon_id, len(word_rows), now)


def _ensure_system_lexicon(connection, entry_count: int, now: str) -> int:
    existing = connection.execute(
        sa.text(
            "select id from lexicon where owner_user_id is null and source_type = :kind"
        ),
        {"kind": SOURCE_TYPE},
    ).fetchone()
    if existing is not None:
        connection.execute(
            sa.text("update lexicon set entry_count = :count where id = :id"),
            {"count": entry_count, "id": existing[0]},
        )
        return existing[0]
    connection.execute(
        sa.text(
            "insert into lexicon (owner_user_id, name, description, visibility, "
            "source_type, entry_count, created_at, updated_at) "
            "values (null, :name, :description, 'public', :kind, :count, :now, :now)"
        ),
        {
            "name": SYSTEM_LEXICON_NAME,
            "description": SYSTEM_LEXICON_DESCRIPTION,
            "kind": SOURCE_TYPE,
            "count": entry_count,
            "now": now,
        },
    )
    return connection.execute(
        sa.text(
            "select id from lexicon where owner_user_id is null and source_type = :kind"
        ),
        {"kind": SOURCE_TYPE},
    ).scalar_one()


def _setting(connection, key: str, fallback: str) -> str:
    row = connection.execute(
        sa.text("select value from app_setting where key = :key"), {"key": key}
    ).fetchone()
    return row[0] if row else fallback


def _int_setting(connection, key: str, fallback: int) -> int:
    try:
        return int(_setting(connection, key, str(fallback)))
    except (TypeError, ValueError):
        return fallback


def _migrate_user_settings(connection, admin_id: int, daily: int, now: str) -> None:
    onboarding = _setting(connection, "onboarding_seen", "false").lower() == "true"
    article_length = _int_setting(connection, "article_length", 650)
    connection.execute(
        sa.text(
            "update user_settings set daily_new_words = :daily, article_length = :length, "
            "onboarding_seen = :seen, updated_at = :now where user_id = :user_id"
        ),
        {
            "daily": daily,
            "length": article_length,
            "seen": 1 if onboarding else 0,
            "now": now,
            "user_id": admin_id,
        },
    )


def _ensure_user_lexicon(
    connection, admin_id: int, lexicon_id: int, daily: int, now: str
) -> None:
    existing = connection.execute(
        sa.text(
            "select id from user_lexicon where user_id = :user_id and lexicon_id = :lexicon_id"
        ),
        {"user_id": admin_id, "lexicon_id": lexicon_id},
    ).fetchone()
    if existing is not None:
        return
    connection.execute(
        sa.text(
            "insert into user_lexicon (user_id, lexicon_id, enabled, daily_new_words, "
            "started_at) values (:user_id, :lexicon_id, 1, :daily, :now)"
        ),
        {"user_id": admin_id, "lexicon_id": lexicon_id, "daily": daily, "now": now},
    )


def _ensure_entry(connection, lexicon_id: int, row) -> int:
    normalized = (row["word"] or "").strip().casefold()
    existing = connection.execute(
        sa.text(
            "select id from lexicon_entry where lexicon_id = :lexicon_id "
            "and normalized_word = :normalized"
        ),
        {"lexicon_id": lexicon_id, "normalized": normalized},
    ).fetchone()
    if existing is not None:
        return existing[0]

    connection.execute(
        sa.text(
            "insert into lexicon_entry (lexicon_id, word, normalized_word, phonetic, "
            "part_of_speech, source_meanings, source_raw, default_anchor, semantic_note, "
            "possible_issue, sequence, frequency_rank, frequency_count, frequency_source, "
            "created_at, updated_at) values (:lexicon_id, :word, :normalized, :phonetic, "
            ":pos, :meanings, :raw, :anchor, :note, :issue, null, null, null, '', "
            ":created_at, :updated_at)"
        ),
        {
            "lexicon_id": lexicon_id,
            "word": row["word"],
            "normalized": normalized,
            "phonetic": row["phonetic"],
            "pos": row["part_of_speech"],
            "meanings": row["source_meanings"],
            "raw": row["source_raw"],
            # The anchor and semantic note are written to BOTH sides: the
            # manually reviewed value stays with the lexicon permanently, and the
            # user's copy can be edited or cleared without losing it.
            "anchor": row["anchor"],
            "note": row["semantic_note"],
            "issue": 1 if row["possible_issue"] else 0,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        },
    )
    return connection.execute(
        sa.text(
            "select id from lexicon_entry where lexicon_id = :lexicon_id "
            "and normalized_word = :normalized"
        ),
        {"lexicon_id": lexicon_id, "normalized": normalized},
    ).scalar_one()


def _ensure_user_word_state(connection, admin_id: int, entry_id: int, row) -> None:
    existing = connection.execute(
        sa.text(
            "select id from user_word_state where user_id = :user_id "
            "and lexicon_entry_id = :entry_id"
        ),
        {"user_id": admin_id, "entry_id": entry_id},
    ).fetchone()
    if existing is not None:
        return
    connection.execute(
        sa.text(
            "insert into user_word_state (user_id, lexicon_entry_id, legacy_word_id, "
            "status, first_seen, last_review, next_review_at, recall_success, recall_fail, "
            "consecutive_failures, context_exposure, anchor_override, semantic_note, "
            "notes, possible_issue, created_at, updated_at) values (:user_id, :entry_id, "
            ":legacy_id, :status, :first_seen, :last_review, :next_review, :success, "
            ":fail, :failures, :exposure, :override, :note, :notes, :issue, "
            ":created_at, :updated_at)"
        ),
        {
            "user_id": admin_id,
            "entry_id": entry_id,
            "legacy_id": row["id"],
            "status": row["status"],
            "first_seen": row["first_seen"],
            "last_review": row["last_review"],
            "next_review": row["next_review_at"],
            "success": row["recall_success"],
            "fail": row["recall_fail"],
            "failures": row["consecutive_failures"],
            "exposure": row["context_exposure"],
            "override": row["anchor"],
            "note": row["semantic_note"],
            "notes": row["notes"],
            "issue": 1 if row["possible_issue"] else 0,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        },
    )


def _backfill_ownership(connection, admin_id: int) -> None:
    connection.execute(
        sa.text("update word set user_id = :user_id where user_id is null"),
        {"user_id": admin_id},
    )
    # Review history keeps status_before/status_after exactly as recorded; the
    # scheduler is never re-run over history.
    connection.execute(
        sa.text("update review_event set user_id = :user_id where user_id is null"),
        {"user_id": admin_id},
    )
    connection.execute(
        sa.text("update article set user_id = :user_id where user_id is null"),
        {"user_id": admin_id},
    )
    connection.execute(
        sa.text("update import_batch set user_id = :user_id where user_id is null"),
        {"user_id": admin_id},
    )
    connection.execute(
        sa.text(
            "update import_candidate set lexicon_entry_id = "
            "(select lexicon_entry_id from word where word.id = import_candidate.word_id) "
            "where lexicon_entry_id is null and word_id is not null"
        )
    )
    placeholders = ", ".join(f":t{index}" for index in range(len(USER_EVENT_TYPES)))
    parameters = {f"t{index}": value for index, value in enumerate(USER_EVENT_TYPES)}
    parameters["user_id"] = admin_id
    connection.execute(
        sa.text(
            f"update history_event set user_id = :user_id where user_id is null "
            f"and event_type in ({placeholders})"
        ),
        parameters,
    )


def _record_audit(connection, admin_id: int, lexicon_id: int, words: int, now: str) -> None:
    counts = {
        "lexicon": connection.execute(sa.text("select count(*) from lexicon")).scalar_one(),
        "lexicon_entry": connection.execute(
            sa.text("select count(*) from lexicon_entry")
        ).scalar_one(),
        "user_word_state": connection.execute(
            sa.text("select count(*) from user_word_state")
        ).scalar_one(),
        "user_lexicon": connection.execute(
            sa.text("select count(*) from user_lexicon")
        ).scalar_one(),
        "migrated_words": words,
        "admin_user_id": admin_id,
        "system_lexicon_id": lexicon_id,
        "frequency_rank_populated": 0,
    }
    import json

    connection.execute(
        sa.text(
            "insert into history_event (user_id, event_type, timestamp, entity_type, "
            "entity_id, payload) values (:user_id, 'v12_migration', :now, 'database', "
            "null, :payload)"
        ),
        {"user_id": admin_id, "now": now, "payload": json.dumps(counts, ensure_ascii=False)},
    )


def upgrade() -> None:
    # Preflight before the first statement of any kind. A migration that fails
    # after it has already added columns and created tables leaves the database
    # in a state that is neither 0004 nor 0005: the revision still says 0004, so
    # re-running fails on the objects that already exist. Checking the legacy data
    # first means the duplicate-word case aborts with zero changes made.
    preflight_normalized_words(op.get_bind())

    _add_compat_columns()
    _create_lexicon_tables()
    _migrate_data()


def downgrade() -> None:
    op.drop_table("user_word_state")
    op.drop_table("user_lexicon")
    op.drop_table("lexicon_entry")
    op.drop_table("lexicon")

    op.drop_index("ix_history_event_user_id", table_name="history_event")
    with op.batch_alter_table("history_event") as batch:
        batch.drop_column("user_id")

    op.drop_index("ix_import_candidate_lexicon_entry_id", table_name="import_candidate")
    with op.batch_alter_table("import_candidate") as batch:
        batch.drop_column("lexicon_entry_id")

    op.drop_index("ix_import_batch_user_id", table_name="import_batch")
    with op.batch_alter_table("import_batch") as batch:
        batch.drop_column("user_id")

    op.drop_index("ix_article_user_id", table_name="article")
    with op.batch_alter_table("article") as batch:
        batch.drop_column("user_id")

    op.drop_index("ix_review_event_user_id", table_name="review_event")
    with op.batch_alter_table("review_event") as batch:
        batch.drop_column("user_id")

    op.drop_index("ix_word_lexicon_entry_id", table_name="word")
    op.drop_index("ix_word_user_id", table_name="word")
    with op.batch_alter_table("word") as batch:
        batch.drop_column("lexicon_entry_id")
        batch.drop_column("user_id")
