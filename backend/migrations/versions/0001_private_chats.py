"""Users, membership and private conversation history."""

import sqlalchemy as sa
from alembic import op

revision = "0001_private_chats"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("username", sa.String(100), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
    )
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
    )
    op.create_table(
        "members",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column(
            "knowledge_base_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_bases.id"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(8), nullable=False),
        sa.CheckConstraint("role IN ('admin','reader')", name="member_role"),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "knowledge_base_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_bases.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_private_history",
        "conversations",
        ["owner_id", "knowledge_base_id", "updated_at"],
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("conversation_id", "position"),
        sa.UniqueConstraint("conversation_id", "request_id", "role"),
        sa.CheckConstraint("role IN ('user','assistant')", name="message_role"),
        sa.CheckConstraint(
            "status IN ('streaming','done','interrupted')", name="message_status"
        ),
    )


def downgrade():
    for table in ["messages", "conversations", "members", "knowledge_bases", "users"]:
        op.drop_table(table)
