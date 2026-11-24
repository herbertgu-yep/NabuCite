"""Track complete vector builds per document."""

import sqlalchemy as sa
from alembic import op

revision = "0005_vector_versions"
down_revision = "0004_library_archive"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "documents",
        sa.Column("vector_versions", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade():
    op.drop_column("documents", "vector_versions")
