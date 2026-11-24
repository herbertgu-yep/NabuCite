"""Keep original question identity and timestamps across answer attempts."""

import sqlalchemy as sa
from alembic import op

revision = "0006_message_attempts"
down_revision = "0005_vector_versions"
branch_labels = depends_on = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("created_at", sa.DateTime(timezone=True)))
    op.add_column("messages", sa.Column("reply_to", sa.Uuid()))
    op.add_column(
        "messages",
        sa.Column("withdrawn", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    for column in ("withdrawn", "reply_to", "created_at"):
        op.drop_column("messages", column)
