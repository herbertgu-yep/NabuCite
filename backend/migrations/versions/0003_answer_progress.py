"""Store observed answer stages alongside their message."""

import sqlalchemy as sa
from alembic import op

revision = "0003_answer_progress"
down_revision = "0002_enterprise"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "messages",
        sa.Column("progress", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade():
    op.drop_column("messages", "progress")
