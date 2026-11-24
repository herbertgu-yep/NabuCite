import argparse
import asyncio
import os
from getpass import getpass
from uuid import UUID

from sqlalchemy import select

from access import DEFAULT_DEPARTMENT
from accounts import check_password
from auth import passwords
from database import DepartmentMember, KnowledgeBase, Member, User, sessions

SHARED = UUID("dbae9397-e759-4cda-ad83-cbcd2151e310")
RESTRICTED = UUID("2e48628e-901d-4e2c-9220-986b46f9cdd5")


async def seed(db, password_for, *, only_if_empty=False):
    async with db.begin() as session:
        from models import initialize_model

        await initialize_model(session)
        # 日常启动只初始化全新数据库，保留管理员后续撤销的成员资格。
        if only_if_empty and (
            await session.scalar(select(User.id).limit(1))
            or await session.scalar(select(KnowledgeBase.id).limit(1))
        ):
            return
        for library_id, name in [(SHARED, "共享知识库"), (RESTRICTED, "受限知识库")]:
            if not await session.get(KnowledgeBase, library_id):
                session.add(KnowledgeBase(id=library_id, name=name))
        await session.flush()
        for username, grants in [
            ("demo_a", [(SHARED, "admin"), (RESTRICTED, "admin")]),
            ("demo_b", [(SHARED, "reader")]),
        ]:
            user = await session.scalar(select(User).where(User.username == username))
            if user is None:
                password = password_for(username)
                if not password:
                    raise ValueError("初始化密码不能为空。")
                check_password(password)
                user = User(username=username, password_hash=passwords.hash(password))
                session.add(user)
                await session.flush()
            if not await session.get(DepartmentMember, user.id):
                session.add(
                    DepartmentMember(user_id=user.id, org_id=DEFAULT_DEPARTMENT)
                )
            for library_id, role in grants:
                if not await session.get(Member, (user.id, library_id)):
                    session.add(
                        Member(user_id=user.id, knowledge_base_id=library_id, role=role)
                    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--if-empty", action="store_true", help="仅为全新数据库创建演示账号和成员关系"
    )
    parser.add_argument(
        "--system-admin", metavar="USERNAME", help="授予已存在账号系统管理权限"
    )
    args = parser.parse_args()
    if args.system_admin:

        async def promote():
            async with sessions().begin() as session:
                user = await session.scalar(
                    select(User).where(User.username == args.system_admin)
                )
                if not user or not user.enabled:
                    raise SystemExit("找不到启用的账号。")
                user.system_admin = True

        asyncio.run(promote())
        raise SystemExit(0)
    asyncio.run(
        seed(
            sessions(),
            lambda name: (
                os.getenv(f"{name.upper()}_PASSWORD")
                or getpass(f"{name} 初始密码（至少 15 字符）：")
            ),
            only_if_empty=args.if_empty,
        )
    )
