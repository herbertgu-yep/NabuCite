from typing import Annotated, Literal
from uuid import UUID

from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr, StringConstraints
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from auth import Identity, passwords
from database import (
    Database,
    DepartmentMember,
    Document,
    KnowledgeBase,
    Member,
    Organization,
    OrgGrant,
    ProjectMember,
    User,
)

router = APIRouter(prefix="/api")
Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]


async def administrator(user: Identity):
    if not user.system_admin:
        raise HTTPException(403, "需要系统管理员权限。")
    return user


Admin = Annotated[User, Depends(administrator)]


def check_password(password):
    folded = password.casefold().strip()
    import re

    predictable = re.fullmatch(r"(.{1,8})\1+", folded) or re.fullmatch(
        r"(?:password|welcome|letmein|qwerty|admin|123456|abcdef|iloveyou)[0-9!@#$._ -]*",
        folded,
    )
    if (
        predictable
        or len(password) < 15
        or len(password) > 1024
        or len(set(folded)) < 4
        or folded
        in {
            "passwordpassword",
            "correct horse battery staple",
            "this is a password",
            "letmeinletmeinletmein",
        }
        or any(
            folded.startswith(prefix)
            for prefix in ("password123", "123456789", "qwertyuiop", "administrator123")
        )
    ):
        raise HTTPException(422, "请使用 15 至 1024 字符且不属于常见弱密码的口令。")


async def profile(session, user):
    dep = await session.scalar(
        select(Organization)
        .join(DepartmentMember)
        .where(DepartmentMember.user_id == user.id)
    )
    groups = await session.scalars(
        select(Organization).join(ProjectMember).where(ProjectMember.user_id == user.id)
    )
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "enabled": user.enabled,
        "system_admin": user.system_admin,
        "department": dep,
        "projects": list(groups),
    }


@router.get("/users/me")
async def me(user: Identity, db: Database):
    async with db() as session:
        return await profile(session, user)


class ProfileEdit(BaseModel):
    display_name: Name | None = None
    username: Name | None = None
    current_password: SecretStr | None = None
    password: SecretStr | None = None


async def unique_username(session):
    try:
        await session.flush()
    except IntegrityError:
        raise HTTPException(409, "登录用户名已被使用。") from None


@router.patch("/users/me")
async def edit_profile(body: ProfileEdit, user: Identity, db: Database):
    async with db.begin() as session:
        row = await session.get(User, user.id, with_for_update=True)
        if (
            body.password is not None
            or body.username is not None
            and body.username != row.username
        ):
            try:
                await run_in_threadpool(
                    passwords.verify,
                    row.password_hash,
                    body.current_password.get_secret_value()
                    if body.current_password
                    else "",
                )
            except VerificationError:
                raise HTTPException(403, "当前密码不正确。") from None
        if body.password is not None:
            value = body.password.get_secret_value()
            check_password(value)
            row.password_hash = await run_in_threadpool(passwords.hash, value)
            row.token_version += 1
        if body.username is not None:
            row.username = body.username
        if body.display_name is not None:
            row.display_name = body.display_name
        await unique_username(session)
        return await profile(session, row)


@router.get("/admin/directory")
async def directory(admin: Admin, db: Database):
    async with db() as session:
        users = await session.scalars(select(User).order_by(User.username))
        orgs = list(
            await session.scalars(select(Organization).order_by(Organization.name))
        )
        grants = [
            {
                "kind": "user",
                "subject": m.user_id,
                "kb": m.knowledge_base_id,
                "role": m.role,
            }
            for m in await session.scalars(select(Member))
        ]
        grants.extend(
            {
                "kind": "organization",
                "subject": m.org_id,
                "kb": m.knowledge_base_id,
                "role": m.role,
            }
            for m in await session.scalars(select(OrgGrant))
        )
        return {
            "users": [await profile(session, u) for u in users],
            "organizations": orgs,
            "libraries": [
                {"id": k.id, "name": k.name, "deleted": k.deleted, "documents": n}
                for k, n in await session.execute(
                    select(KnowledgeBase, func.count(Document.id))
                    .outerjoin(Document)
                    .group_by(KnowledgeBase.id)
                )
            ],
            "grants": grants,
        }


class OrgInput(BaseModel):
    kind: Literal["department", "project"]
    name: Name


@router.post("/admin/organizations", status_code=201)
async def add_org(body: OrgInput, admin: Admin, db: Database):
    async with db.begin() as session:
        org = Organization(**body.model_dump())
        session.add(org)
        try:
            await session.flush()
        except IntegrityError:
            raise HTTPException(409, "同类组织名称已存在。") from None
        return org


class Employee(BaseModel):
    username: Name
    display_name: Name
    department_id: UUID
    projects: list[UUID] = Field(default_factory=list)
    password: SecretStr | None = None
    enabled: bool = True


@router.put("/admin/employees/{employee_id}")
async def employee(employee_id: UUID, body: Employee, admin: Admin, db: Database):
    async with db.begin() as session:
        dep = await session.get(Organization, body.department_id)
        groups = set(body.projects)
        valid = set(
            await session.scalars(
                select(Organization.id).where(
                    Organization.kind == "project", Organization.id.in_(groups)
                )
            )
        )
        if not dep or dep.kind != "department" or groups != valid:
            raise HTTPException(422, "部门或项目组无效。")
        if not body.enabled:
            active_admins = set(
                await session.scalars(
                    select(User.id)
                    .where(User.enabled, User.system_admin)
                    .order_by(User.id)
                    .with_for_update()
                )
            )
            if admin.id not in active_admins or active_admins == {employee_id}:
                raise HTTPException(409, "必须保留有效管理员。")
        row = await session.get(User, employee_id, with_for_update=True)
        if row is None:
            if not body.password:
                raise HTTPException(422, "新员工需要初始密码。")
            row = User(id=employee_id, token_version=0)
            session.add(row)
        if admin.id == row.id and (
            body.password is not None or body.username != row.username
        ):
            raise HTTPException(403, "请到个人中心验证当前密码后修改自己的登录信息。")
        if admin.id == row.id and not body.enabled:
            raise HTTPException(409, "无法停用当前管理员。")
        if body.password is not None:
            value = body.password.get_secret_value()
            check_password(value)
            row.password_hash = await run_in_threadpool(passwords.hash, value)
        if body.password or row.enabled and not body.enabled:
            row.token_version += 1
        row.username, row.display_name, row.enabled = (
            body.username,
            body.display_name,
            body.enabled,
        )
        await unique_username(session)
        await session.merge(DepartmentMember(user_id=row.id, org_id=dep.id))
        await session.execute(
            delete(ProjectMember).where(ProjectMember.user_id == row.id)
        )
        session.add_all(ProjectMember(user_id=row.id, org_id=g) for g in groups)
        return {"id": row.id}


class Grant(BaseModel):
    role: Literal["reader", "admin"] | None


@router.put("/admin/grants/{kind}/{subject}/{kb_id}")
async def grant(
    kind: Literal["user", "organization"],
    subject: UUID,
    kb_id: UUID,
    body: Grant,
    admin: Admin,
    db: Database,
):
    async with db.begin() as session:
        principal = await session.get(
            User if kind == "user" else Organization, subject, with_for_update=True
        )
        if not principal or not await session.get(KnowledgeBase, kb_id):
            raise HTTPException(404, "主体或知识库不存在。")
        table = Member if kind == "user" else OrgGrant
        row = await session.get(table, (subject, kb_id))
        if body.role is None:
            if row:
                await session.delete(row)
        elif row:
            row.role = body.role
        else:
            session.add(
                table(
                    knowledge_base_id=kb_id,
                    role=body.role,
                    **{"user_id" if kind == "user" else "org_id": subject},
                )
            )
        return {"role": body.role}


class LibraryInput(BaseModel):
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)
    ]


@router.post("/admin/libraries", status_code=201)
async def new_library(body: LibraryInput, admin: Admin, db: Database):
    async with db.begin() as session:
        kb = KnowledgeBase(name=body.name)
        session.add(kb)
        await session.flush()
        session.add(Member(user_id=admin.id, knowledge_base_id=kb.id, role="admin"))
        return {"id": kb.id}


class LibraryEdit(BaseModel):
    name: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)
        ]
        | None
    ) = None
    deleted: bool | None = None


@router.patch("/admin/libraries/{kb_id}")
async def edit_library(kb_id: UUID, body: LibraryEdit, admin: Admin, db: Database):
    async with db.begin() as session:
        kb = await session.get(KnowledgeBase, kb_id, with_for_update=True)
        if not kb:
            raise HTTPException(404, "知识库不存在。")
        for field, value in body.model_dump(exclude_none=True).items():
            setattr(kb, field, value)
        return {"id": kb.id}


class BatchEmployees(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=500)
    action: Literal["department", "join", "leave", "enable", "disable"]
    org_id: UUID | None = None


@router.post("/admin/employees/batch")
async def batch_employees(body: BatchEmployees, admin: Admin, db: Database):
    failed = {}
    success = []
    async with db.begin() as session:
        # 固定管理员锁顺序，避免两名操作者同时停用对方后没有可用管理员。
        admins = set(
            await session.scalars(
                select(User.id)
                .where(User.enabled, User.system_admin)
                .order_by(User.id)
                .with_for_update()
            )
        )
        if admin.id not in admins:
            raise HTTPException(403, "管理员已停用。")
        if body.action in {"department", "join", "leave"}:
            org = await session.get(Organization, body.org_id) if body.org_id else None
            if not org or org.kind != (
                "department" if body.action == "department" else "project"
            ):
                raise HTTPException(422, "请选择有效组织。")
        for uid in sorted(set(body.ids)):
            row = await session.get(User, uid, with_for_update=True)
            if not row:
                failed[str(uid)] = "员工不存在"
                continue
            if body.action == "disable" and (uid == admin.id or admins == {uid}):
                failed[str(uid)] = "需保留当前操作者和有效管理员"
                continue
            if body.action in {"enable", "disable"}:
                if row.enabled and body.action == "disable":
                    row.token_version += 1
                row.enabled = body.action == "enable"
                if not row.enabled:
                    admins.discard(uid)
            elif body.action == "department":
                await session.merge(DepartmentMember(user_id=uid, org_id=body.org_id))
            elif body.action == "join":
                await session.merge(ProjectMember(user_id=uid, org_id=body.org_id))
            else:
                await session.execute(
                    delete(ProjectMember).where(
                        ProjectMember.user_id == uid,
                        ProjectMember.org_id == body.org_id,
                    )
                )
            success.append(uid)
    return {"success": success, "failed": failed}
