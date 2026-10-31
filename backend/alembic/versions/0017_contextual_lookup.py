"""Cache each word separately in each article sentence, preserving prior lookups."""
from alembic import op
import sqlalchemy as sa

revision = "0017_contextual_lookup"
down_revision = "0016_dictionary_extraction"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("article_word_lookup", recreate="always") as batch:
        batch.drop_constraint("uq_article_lookup_word", type_="unique")
        batch.create_unique_constraint("uq_article_lookup_context", ["article_id", "normalized_word", "context", "source"])


def downgrade():
    duplicate = op.get_bind().execute(sa.text(
        "select 1 from article_word_lookup group by article_id, normalized_word having count(*) > 1 limit 1"
    )).first()
    if duplicate:
        raise RuntimeError("Cannot downgrade: distinct sentence explanations would be lost")
    with op.batch_alter_table("article_word_lookup", recreate="always") as batch:
        batch.drop_constraint("uq_article_lookup_context", type_="unique")
        batch.create_unique_constraint("uq_article_lookup_word", ["article_id", "normalized_word"])
