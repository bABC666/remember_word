"""Store full source extraction separately from confirmed core meanings."""
from alembic import op
import sqlalchemy as sa

revision = '0016_dictionary_extraction'
down_revision = '0015_session_autoincrement'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('entry_dictionary_extraction',
        sa.Column('lexicon_entry_id', sa.Integer(),
                  sa.ForeignKey('lexicon_entry.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('parser_version', sa.String(64), nullable=False),
        sa.Column('payload_sha256', sa.String(64), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False))


def downgrade():
    raise RuntimeError('Dictionary extraction downgrade refused: use batch rollback; preserve evidence')
