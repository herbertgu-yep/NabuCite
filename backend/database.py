from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import database_url


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(default="")
    system_admin: Mapped[bool] = mapped_column(default=False)
    enabled: Mapped[bool] = mapped_column(default=True)
    token_version: Mapped[int] = mapped_column(default=0)


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    deleted: Mapped[bool] = mapped_column(default=False, server_default="false")
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(160))


class Member(Base):
    __tablename__ = "members"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(8))
    __table_args__ = (
        CheckConstraint("role IN ('admin','reader')", name="member_role"),
    )


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    knowledge_base_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_bases.id"))
    archived: Mapped[bool] = mapped_column(default=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    summary_position: Mapped[int] = mapped_column(default=0)
    title: Mapped[str] = mapped_column(String(100), default="新对话")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    __table_args__ = (
        Index("ix_private_history", "owner_id", "knowledge_base_id", "updated_at"),
    )


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"))
    position: Mapped[int]
    request_id: Mapped[UUID]
    role: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12))
    error: Mapped[str | None] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(default="general")
    progress: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    citations: Mapped[list] = mapped_column(JSON, default=list)
    model_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_versions.id")
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    withdrawn: Mapped[bool] = mapped_column(default=False, server_default="false")
    reply_to: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("conversation_id", "position"),
        UniqueConstraint("conversation_id", "request_id", "role"),
        CheckConstraint("role IN ('user','assistant')", name="message_role"),
        CheckConstraint(
            "status IN ('streaming','done','interrupted')", name="message_status"
        ),
    )


@lru_cache
def sessions() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        create_async_engine(database_url(), hide_parameters=True, pool_pre_ping=True),
        expire_on_commit=False,
    )


Database = Annotated[async_sessionmaker[AsyncSession], Depends(sessions)]


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    kind: Mapped[str]
    name: Mapped[str]
    __table_args__ = (
        UniqueConstraint("kind", "name"),
        CheckConstraint("kind in ('department','project')"),
    )


class DepartmentMember(Base):
    __tablename__ = "department_members"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))


class ProjectMember(Base):
    __tablename__ = "project_members"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), primary_key=True
    )


class OrgGrant(Base):
    __tablename__ = "org_grants"
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), primary_key=True
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id"), primary_key=True
    )
    role: Mapped[str]
    __table_args__ = (CheckConstraint("role in ('admin','reader')"),)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id"), index=True
    )
    object_key: Mapped[str] = mapped_column(unique=True)
    filename: Mapped[str]
    status: Mapped[str] = mapped_column(default="pending")
    failure: Mapped[str | None]
    vector_versions: Mapped[list] = mapped_column(
        JSON, default=list, server_default="[]"
    )
    chunk_count: Mapped[int] = mapped_column(default=0)
    attempt: Mapped[UUID] = mapped_column(default=uuid4)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    parent_id: Mapped[UUID | None]
    ordinal: Mapped[int | None]
    __table_args__ = (
        Index("ix_chunk_parent_order", "document_id", "parent_id", "ordinal"),
    )
    section: Mapped[str]
    page_start: Mapped[int]
    page_end: Mapped[int]
    text: Mapped[str] = mapped_column(Text)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    base_url: Mapped[str]
    model: Mapped[str]
    credential: Mapped[str]
    window: Mapped[int]
    output: Mapped[int]
    published: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Publication(Base):
    __tablename__ = "publication"
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("model_versions.id"))


class Usage(Base):
    __tablename__ = "model_usage"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    department_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    answer_id: Mapped[UUID | None] = mapped_column(ForeignKey("messages.id"))
    version_id: Mapped[UUID] = mapped_column(ForeignKey("model_versions.id"))
    purpose: Mapped[str]
    source: Mapped[str] = mapped_column(default="unknown")
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
