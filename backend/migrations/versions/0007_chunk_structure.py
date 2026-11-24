"""Persist structural parents and source order for newly parsed documents."""

import sqlalchemy as sa
from alembic import op

revision = "0007_chunk_structure"
down_revision = "0006_message_attempts"
branch_labels = depends_on = None


def upgrade():
    op.add_column("chunks", sa.Column("parent_id", sa.Uuid()))
    op.add_column("chunks", sa.Column("ordinal", sa.Integer()))
    op.create_index(
        "ix_chunk_parent_order", "chunks", ["document_id", "parent_id", "ordinal"]
    )


def downgrade():
    op.drop_index("ix_chunk_parent_order", table_name="chunks")
    op.drop_column("chunks", "ordinal")
    op.drop_column("chunks", "parent_id")
