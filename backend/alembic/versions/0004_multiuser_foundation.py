"""Add users, server-side sessions and per-user settings.

Revision ID: 0004_multiuser_foundation
Revises: 0003_article_reading_tools

The bootstrap account is created with the unusable password sentinel ``!`` so no
password material is ever committed to the repository. Set a real password with
``python -m app.cli set-password <username>``.

The migration is idempotent: re-running it will not duplicate the bootstrap
account or overwrite a password that has already been set.
"""

import os
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "0004_multiuser_foundation"
down_revision = "0003_article_reading_tools"
branch_labels = None
depends_on = None

UNUSABLE_PASSWORD = "!"


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False, server_default=""),
        # Stores an Argon2id hash, or "!" while the password is unset.
        sa.Column("password_hash", sa.String(length=255), nullable=False, server_default="!"),
        sa.Column("role", sa.String(length=24), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_username", "user", ["username"], unique=True)
    op.create_index("ix_user_role", "user", ["role"])

    op.create_table(
        "user_session",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Only the SHA-256 digest of the opaque token is persisted; the raw token
        # exists solely in the caller's HttpOnly cookie.
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=False, server_default=""),
    )
    op.create_index("ix_user_session_user_id", "user_session", ["user_id"])
    op.create_index("ix_user_session_token_hash", "user_session", ["token_hash"], unique=True)
    op.create_index("ix_user_session_expires_at", "user_session", ["expires_at"])
    op.create_index("ix_user_session_revoked_at", "user_session", ["revoked_at"])

    op.create_table(
        "user_settings",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("daily_new_words", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("article_length", sa.Integer(), nullable=False, server_default="650"),
        sa.Column("onboarding_seen", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("theme", sa.String(length=16), nullable=False, server_default="auto"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    _create_bootstrap_admin()


def _create_bootstrap_admin() -> None:
    username = (os.getenv("VOCAB_BOOTSTRAP_USERNAME") or "admin").strip().casefold() or "admin"
    connection = op.get_bind()
    existing = connection.execute(
        sa.text("select id from user where username = :username"), {"username": username}
    ).fetchone()
    if existing is not None:
        # Idempotent: never touch an account that already exists, and never
        # reset a password an operator has already set.
        return

    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    connection.execute(
        sa.text(
            "insert into user (username, display_name, password_hash, role, is_active, "
            "created_at, updated_at) values (:username, :display_name, :password_hash, "
            "'admin', 1, :now, :now)"
        ),
        {
            "username": username,
            "display_name": username,
            "password_hash": UNUSABLE_PASSWORD,
            "now": now,
        },
    )
    user_id = connection.execute(
        sa.text("select id from user where username = :username"), {"username": username}
    ).scalar_one()
    connection.execute(
        sa.text(
            "insert into user_settings (user_id, daily_new_words, article_length, "
            "onboarding_seen, theme, created_at, updated_at) "
            "values (:user_id, 15, 650, 0, 'auto', :now, :now)"
        ),
        {"user_id": user_id, "now": now},
    )


def downgrade() -> None:
    op.drop_table("user_settings")
    op.drop_table("user_session")
    op.drop_table("user")
