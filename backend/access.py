from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import literal, select, union_all

from database import (
    DepartmentMember,
    KnowledgeBase,
    Member,
    Organization,
    OrgGrant,
    ProjectMember,
    User,
)

DEFAULT_DEPARTMENT = UUID("b77531f3-a628-4495-b384-c92aae105775")


def sources(user_id):
    direct = select(
        Member.knowledge_base_id,
        Member.role,
        literal("个人授权").label("source"),
        literal("user").label("kind"),
    ).where(Member.user_id == user_id)
    inherited = [
        select(
            OrgGrant.knowledge_base_id,
            OrgGrant.role,
            Organization.name.label("source"),
            Organization.kind,
        )
        .join(Organization, Organization.id == OrgGrant.org_id)
        .join(member, member.org_id == OrgGrant.org_id)
        .where(member.user_id == user_id)
        for member in (DepartmentMember, ProjectMember)
    ]
    return union_all(direct, *inherited).subquery()


async def authorized_libraries(session, user_id, kb_id=None):
    grants = sources(user_id)
    stmt = (
        select(KnowledgeBase, grants.c.role, grants.c.source, grants.c.kind)
        .join(grants, grants.c.knowledge_base_id == KnowledgeBase.id)
        .where(KnowledgeBase.deleted.is_(False))
        .order_by(KnowledgeBase.name)
    )
    if kb_id:
        stmt = stmt.where(KnowledgeBase.id == kb_id)
    result = {}
    for kb, role, source, kind in await session.execute(stmt):
        row = result.setdefault(
            kb.id, {"id": kb.id, "name": kb.name, "role": "reader", "sources": []}
        )
        row["sources"].append({"name": source, "role": role, "kind": kind})
        if role == "admin":
            row["role"] = role
    return list(result.values())


async def membership(session, user_id, kb_id, *, admin=False):
    if admin:
        principal = await session.get(User, user_id)
        if principal and principal.enabled and principal.system_admin:
            kb = await session.get(KnowledgeBase, kb_id, with_for_update=True)
            if kb and not kb.deleted:
                return {"id": kb.id, "name": kb.name}
            raise HTTPException(404, "知识库不存在或不可访问。")
    rows = await authorized_libraries(session, user_id, kb_id)
    if not rows:
        raise HTTPException(404, "知识库不存在或不可访问。")
    if admin:
        raise HTTPException(403, "需要系统管理员权限。")
    return rows[0]
