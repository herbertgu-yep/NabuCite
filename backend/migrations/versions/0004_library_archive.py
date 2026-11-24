"""Keep archived libraries and their private history."""

import sqlalchemy as sa
from alembic import op

revision = "0004_library_archive"
down_revision = "0003_answer_progress"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "knowledge_bases",
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("knowledge_bases", "deleted")
