"""Enterprise documents, access sources and model accounting."""

import sqlalchemy as sa
from alembic import op

revision = "0002_enterprise"
down_revision = "0001_private_chats"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("base_url", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("credential", sa.String(), nullable=False),
        sa.Column("window", sa.Integer(), nullable=False),
        sa.Column("output", sa.Integer(), nullable=False),
        sa.Column("published", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.CheckConstraint("kind in ('department','project')"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "name"),
    )
    op.create_table(
        "department_members",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("failure", sa.String(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index(
        op.f("ix_documents_knowledge_base_id"),
        "documents",
        ["knowledge_base_id"],
        unique=False,
    )
    op.create_table(
        "org_grants",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.CheckConstraint("role in ('admin','reader')"),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
        ),
        sa.PrimaryKeyConstraint("org_id", "knowledge_base_id"),
    )
    op.create_table(
        "project_members",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("user_id", "org_id"),
    )
    op.create_table(
        "publication",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["model_versions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("section", sa.String(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_chunks_document_id"), "chunks", ["document_id"], unique=False
    )
    op.create_table(
        "model_usage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("answer_id", sa.Uuid(), nullable=True),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["answer_id"],
            ["messages.id"],
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["organizations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["model_versions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_model_usage_created_at"), "model_usage", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_model_usage_user_id"), "model_usage", ["user_id"], unique=False
    )
    op.add_column(
        "conversations",
        sa.Column("archived", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "conversations",
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "conversations",
        sa.Column("summary_position", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "messages",
        sa.Column("mode", sa.String(), server_default="general", nullable=False),
    )
    op.add_column(
        "messages",
        sa.Column("citations", sa.JSON(), server_default="[]", nullable=False),
    )
    op.add_column("messages", sa.Column("model_version_id", sa.Uuid(), nullable=True))
    if op.get_context().dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_message_model_version",
            "messages",
            "model_versions",
            ["model_version_id"],
            ["id"],
        )
    op.add_column(
        "users",
        sa.Column("display_name", sa.String(), server_default="", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column(
            "system_admin", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.add_column(
        "users",
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), server_default="0", nullable=False),
    )

    org = sa.table(
        "organizations",
        sa.column("id", sa.Uuid()),
        sa.column("kind", sa.String()),
        sa.column("name", sa.String()),
    )
    from uuid import UUID

    default_id = UUID("b77531f3-a628-4495-b384-c92aae105775")
    op.bulk_insert(
        org, [{"id": default_id, "kind": "department", "name": "待分配部门"}]
    )
    users = sa.table("users", sa.column("id", sa.Uuid()))
    members = sa.table(
        "department_members",
        sa.column("user_id", sa.Uuid()),
        sa.column("org_id", sa.Uuid()),
    )
    op.execute(
        members.insert().from_select(
            ["user_id", "org_id"],
            sa.select(users.c.id, sa.literal(default_id, type_=sa.Uuid())),
        )
    )


def downgrade():
    op.drop_column("users", "token_version")
    op.drop_column("users", "enabled")
    op.drop_column("users", "system_admin")
    op.drop_column("users", "display_name")
    if op.get_context().dialect.name != "sqlite":
        op.drop_constraint("fk_message_model_version", "messages", type_="foreignkey")
    op.drop_column("messages", "model_version_id")
    op.drop_column("messages", "citations")
    op.drop_column("messages", "mode")
    op.drop_column("conversations", "summary_position")
    op.drop_column("conversations", "summary")
    op.drop_column("conversations", "archived")
    op.drop_index(op.f("ix_model_usage_user_id"), table_name="model_usage")
    op.drop_index(op.f("ix_model_usage_created_at"), table_name="model_usage")
    op.drop_table("model_usage")
    op.drop_index(op.f("ix_chunks_document_id"), table_name="chunks")
    op.drop_table("chunks")
    op.drop_table("publication")
    op.drop_table("project_members")
    op.drop_table("org_grants")
    op.drop_index(op.f("ix_documents_knowledge_base_id"), table_name="documents")
    op.drop_table("documents")
    op.drop_table("department_members")
    op.drop_table("organizations")
    op.drop_table("model_versions")
