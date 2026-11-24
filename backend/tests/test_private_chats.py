import asyncio
import json
import os
import secrets
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import jwt
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config import database_url, model
from database import KnowledgeBase, Member, Message, User, sessions
from main import app
from model_stream import ModelFailure
from seed import RESTRICTED, SHARED, seed


def upgrade(connection):
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, "head")


class ChatFixture(unittest.IsolatedAsyncioTestCase):
    postgres = False

    async def asyncSetUp(self):
        if self.postgres:
            namespace = "qa_" + uuid4().hex
            url = database_url()
            admin = create_async_engine(url)
            async with admin.begin() as conn:
                await conn.execute(text(f'CREATE SCHEMA "{namespace}"'))

            async def drop_schema():
                async with admin.begin() as conn:
                    await conn.execute(text(f'DROP SCHEMA "{namespace}" CASCADE'))
                await admin.dispose()

            self.addAsyncCleanup(drop_schema)
            self.engine = create_async_engine(
                url, connect_args={"server_settings": {"search_path": namespace}}
            )
        else:
            directory = tempfile.TemporaryDirectory()
            self.addCleanup(directory.cleanup)
            self.engine = create_async_engine(
                "sqlite+aiosqlite:///" + str(Path(directory.name) / "qa.db")
            )

            @event.listens_for(self.engine.sync_engine, "connect")
            def foreign_keys(conn, _):
                conn.execute("PRAGMA foreign_keys=ON")

        self.addAsyncCleanup(self.engine.dispose)
        async with self.engine.begin() as conn:
            await conn.run_sync(upgrade)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)
        self.secret = secrets.token_urlsafe(32)
        self.password = secrets.token_urlsafe(20)
        self.enterContext(patch.dict(os.environ, {"JWT_SECRET": self.secret}))
        self.enterContext(
            patch.object(model, "api_key", SecretStr("integration-test-only"))
        )
        self.enterContext(
            patch.dict(app.dependency_overrides, {sessions: lambda: self.db})
        )
        await seed(self.db, lambda _: self.password, only_if_empty=True)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self.addAsyncCleanup(self.client.aclose)
        self.a, self.b = await self.login("demo_a"), await self.login("demo_b")
        self.library = f"/api/knowledge-bases/{SHARED}"
        self.chats = self.library + "/conversations"

    async def login(self, username):
        result = await self.client.post(
            "/api/auth/login", json={"username": username, "password": self.password}
        )
        self.assertEqual(result.status_code, 200)
        self.assertGreater(result.json()["expires_in"], 0)
        return {"Authorization": "Bearer " + result.json()["access_token"]}

    async def new_chat(self, who):
        response = await self.client.post(self.chats, headers=who)
        self.assertEqual(response.status_code, 201)
        return "/api/conversations/" + response.json()["id"] + "/messages"


class PrivateChats(ChatFixture):
    async def test_first_turn_is_atomic_and_retry_reads_the_same_answer(self):
        path = f"/api/conversations/{uuid4()}/messages"
        body = {"first_in": str(SHARED), "question": "首问", "request_id": str(uuid4())}
        with patch.object(model, "api_key", SecretStr("")):
            self.assertEqual(
                (await self.client.post(path, headers=self.a, json=body)).status_code,
                503,
            )
        self.assertEqual((await self.client.get(self.chats, headers=self.a)).json(), [])
        calls = []

        async def upstream(*_):
            calls.append(1)
            yield "完成"

        with patch("chat.stream_reply", upstream):
            sent = await self.client.post(path, headers=self.a, json=body)
            repeat = await self.client.post(path, headers=self.a, json=body)
            self.assertEqual(
                (await self.client.post(path, headers=self.b, json=body)).status_code,
                404,
            )
        steps = [
            json.loads(b.split("data: ")[1])
            for b in sent.text.strip().split("\n\n")
            if b.startswith("event: progress")
        ]
        self.assertEqual(
            [(s["stage"], s["status"], s["seq"]) for s in steps],
            [
                ("wait", "active", 1),
                ("wait", "complete", 2),
                ("answer", "active", 3),
                ("answer", "complete", 4),
            ],
        )
        self.assertEqual(repeat.json()["detail"]["code"], "request_exists")
        rows = (await self.client.get(path, headers=self.a)).json()
        self.assertEqual([r["content"] for r in rows], ["首问", "完成"])
        self.assertEqual(
            [step["id"] for step in rows[-1]["progress"]], ["wait-1", "answer-3"]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            len((await self.client.get(self.chats, headers=self.a)).json()), 1
        )

    async def test_withdraw_before_admission_and_regenerate(self):
        path = f"/api/conversations/{uuid4()}"
        body = {
            "first_in": str(SHARED),
            "question": "保留此问题",
            "request_id": str(uuid4()),
        }
        for _ in range(2):
            self.assertEqual(
                (
                    await self.client.post(
                        path + "/withdraw", headers=self.a, json=body
                    )
                ).status_code,
                200,
            )
        self.assertEqual(
            (
                await self.client.post(path + "/messages", headers=self.a, json=body)
            ).status_code,
            409,
        )
        self.assertEqual(
            (
                await self.client.post(path + "/withdraw", headers=self.b, json=body)
            ).status_code,
            404,
        )
        rows = (await self.client.get(path + "/messages", headers=self.a)).json()
        self.assertEqual([r["status"] for r in rows], ["done", "withdrawn"])
        self.assertTrue(rows[0]["created_at"])
        retry = {
            "request_id": str(uuid4()),
            "question": body["question"],
            "regenerate": rows[0]["id"],
        }

        async def upstream(*args):
            yield "新的回答"

        with patch("chat.stream_reply", upstream):
            self.assertIn(
                "event: done",
                (
                    await self.client.post(
                        path + "/messages", headers=self.a, json=retry
                    )
                ).text,
            )
        self.assertEqual(
            (
                await self.client.post(path + "/withdraw", headers=self.a, json=retry)
            ).status_code,
            200,
        )
        rows = (await self.client.get(path + "/messages", headers=self.a)).json()
        self.assertEqual([r["role"] for r in rows], ["user", "assistant", "assistant"])
        self.assertEqual([r["content"] for r in rows], [body["question"], "", ""])
        self.assertEqual(rows[-1]["reply_to"], rows[0]["id"])

    async def test_withdraw_active_stream(self):
        for partial in (False, True):
            path = await self.new_chat(self.a)
            reached = asyncio.Event()

            async def upstream(*args, partial=partial, reached=reached):
                if partial:
                    yield "尚未完成"
                reached.set()
                await asyncio.Future()

            body = {"request_id": str(uuid4()), "question": "保留问题"}
            with patch("chat.stream_reply", upstream):
                pending = asyncio.create_task(
                    self.client.post(path, headers=self.a, json=body)
                )
                try:
                    await asyncio.wait_for(reached.wait(), 8)
                    r = await self.client.post(
                        path.removesuffix("/messages") + "/withdraw",
                        headers=self.a,
                        json=body,
                    )
                    self.assertEqual(r.status_code, 200)
                    await asyncio.wait_for(
                        asyncio.gather(pending, return_exceptions=True), 5
                    )
                finally:
                    pending.cancel()
            rows = (await self.client.get(path, headers=self.a)).json()
            self.assertEqual(
                (rows[-1]["status"], rows[-1]["content"], rows[-1]["citations"]),
                ("withdrawn", "", []),
            )

    async def test_seed_login_and_roles(self):
        await seed(self.db, lambda _: self.fail("已有用户不应再读取密码"))
        async with self.db() as session:
            counts = [
                await session.scalar(select(func.count()).select_from(table))
                for table in [User, KnowledgeBase, Member]
            ]
            self.assertEqual(counts, [2, 2, 3])
        me = (
            await self.client.get("/api/users/me", headers=await self.login("demo_a"))
        ).json()
        self.assertEqual(me["username"], "demo_a")
        now = datetime.now(UTC)
        expired = jwt.encode(
            {
                "iss": "nabucite",
                "sub": me["id"],
                "iat": now - timedelta(hours=2),
                "exp": now - timedelta(seconds=1),
            },
            self.secret,
            algorithm="HS256",
        )
        for headers in [
            {},
            {"Authorization": "Bearer invalid"},
            {"Authorization": "Bearer " + expired},
        ]:
            self.assertEqual(
                (await self.client.get("/api/users/me", headers=headers)).status_code,
                401,
            )
        for username in ["demo_a", "absent"]:
            self.assertEqual(
                (
                    await self.client.post(
                        "/api/auth/login",
                        json={"username": username, "password": "invalid"},
                    )
                ).status_code,
                401,
            )
        for headers, roles in [
            (self.a, {str(SHARED): "admin", str(RESTRICTED): "admin"}),
            (self.b, {str(SHARED): "reader"}),
        ]:
            result = await self.client.get("/api/knowledge-bases", headers=headers)
            self.assertEqual({row["id"]: row["role"] for row in result.json()}, roles)
        self.assertEqual(
            (
                await self.client.patch(
                    self.library, headers=self.a, json={"name": "   "}
                )
            ).status_code,
            422,
        )
        self.assertEqual(
            (
                await self.client.patch(
                    self.library, headers=self.a, json={"name": "新名称"}
                )
            ).status_code,
            403,
        )
        self.assertEqual(
            (
                await self.client.patch(
                    self.library, headers=self.b, json={"name": "读者修改"}
                )
            ).status_code,
            403,
        )
        self.assertEqual(
            (await self.client.get(self.library, headers=self.b)).json()["name"],
            "共享知识库",
        )
        for kb in [RESTRICTED, uuid4()]:
            base = f"/api/knowledge-bases/{kb}"
            self.assertEqual(
                (await self.client.get(base, headers=self.b)).status_code, 404
            )
            self.assertEqual(
                (
                    await self.client.patch(base, headers=self.b, json={"name": "x"})
                ).status_code,
                404,
            )
            self.assertEqual(
                (
                    await self.client.post(base + "/conversations", headers=self.b)
                ).status_code,
                404,
            )

    async def test_private_and_revocation(self):
        target = await self.new_chat(self.a)
        reader_chat = await self.new_chat(self.b)
        self.assertEqual(
            len((await self.client.get(self.chats, headers=self.b)).json()), 1
        )
        self.assertNotEqual(target, reader_chat)
        body = {"question": "禁止发送", "request_id": str(uuid4())}
        with patch(
            "chat.stream_reply", side_effect=AssertionError("拒绝请求不应调用模型")
        ) as upstream:
            for who, path in [(self.b, target), (self.a, reader_chat)]:
                self.assertEqual(
                    (await self.client.get(path, headers=who)).status_code, 404
                )
                self.assertEqual(
                    (await self.client.post(path, headers=who, json=body)).status_code,
                    404,
                )
            uid = UUID(
                (await self.client.get("/api/users/me", headers=self.a)).json()["id"]
            )
            async with self.db.begin() as session:
                await session.execute(
                    delete(Member).where(
                        Member.user_id == uid, Member.knowledge_base_id == SHARED
                    )
                )
            await seed(
                self.db, lambda _: self.fail("日常启动不应重置账号"), only_if_empty=True
            )
            for path in [self.library, self.chats, target]:
                self.assertEqual(
                    (await self.client.get(path, headers=self.a)).status_code, 404
                )
            self.assertEqual(
                (await self.client.post(target, headers=self.a, json=body)).status_code,
                404,
            )
            upstream.assert_not_called()
        async with self.db() as session:
            self.assertEqual(
                await session.scalar(select(func.count()).select_from(Message)), 0
            )

    async def test_stream_refresh_retry_context_and_failure(self):
        target, other = await self.new_chat(self.a), await self.new_chat(self.a)
        started, resume = asyncio.Event(), asyncio.Event()
        calls = []

        async def upstream(messages, *_):
            calls.append(messages)
            if messages[-1]["content"] == "一问":
                yield "部分"
                started.set()
                await resume.wait()
                yield "完成"
            elif messages[-1]["content"] == "出错":
                yield "保留"
                raise ModelFailure("模型凭据无效")
            else:
                yield "正文"

        first = {"request_id": str(uuid4()), "question": "一问"}

        def ask(path, question):
            return self.client.post(
                path,
                headers=self.a,
                json={"request_id": str(uuid4()), "question": question},
            )

        with patch("chat.stream_reply", upstream):
            owner = asyncio.create_task(
                self.client.post(target, headers=self.a, json=first)
            )
            try:
                await asyncio.wait_for(started.wait(), 5)
                visible = (await self.client.get(target, headers=self.a)).json()
                self.assertEqual(
                    (visible[-1]["content"], visible[-1]["status"]),
                    ("部分", "streaming"),
                )
                self.assertEqual(
                    (
                        await self.client.post(target, headers=self.a, json=first)
                    ).status_code,
                    409,
                )
                self.assertEqual((await ask(target, "竞争")).status_code, 409)
                self.assertIn("event: done", (await ask(other, "另一会话")).text)
            finally:
                resume.set()
                done = await owner
            self.assertIn("event: done", done.text)
            self.assertEqual(
                (
                    await self.client.post(target, headers=self.a, json=first)
                ).status_code,
                409,
            )
            self.assertIn("event: error", (await ask(target, "出错")).text)
            fresh = await self.login("demo_a")
            follow = await self.client.post(
                target,
                headers=fresh,
                json={"question": "追问", "request_id": str(uuid4())},
            )
            self.assertIn("event: done", follow.text)
            self.assertEqual(
                [m["content"] for m in calls[-1]],
                ["一问", "部分完成", "追问"],
            )
            self.assertEqual(len(calls), 4)
            rows = (await self.client.get(target, headers=fresh)).json()
            self.assertEqual([r["position"] for r in rows], list(range(1, 7)))
            self.assertEqual(rows[3]["status"], "interrupted")
            self.assertEqual(rows[-1]["status"], "done")

    async def test_http_disconnect_preserves_committed_delta(self):
        path = await self.new_chat(self.a)
        body = json.dumps({"request_id": str(uuid4()), "question": "关闭页面"}).encode()
        delivered = False
        disconnect = asyncio.Event()

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def sent(frame):
            if b"event: delta" in frame.get("body", b""):
                disconnect.set()

        async def upstream(*_):
            yield "保留的正文"
            await asyncio.Event().wait()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"authorization", self.a["Authorization"].encode()),
            ],
            "server": ("test", 80),
            "client": ("test", 1234),
        }
        with patch("chat.stream_reply", upstream):
            await asyncio.wait_for(app(scope, receive, sent), 5)
        rows = (await self.client.get(path, headers=self.a)).json()
        self.assertEqual(
            (rows[-1]["status"], rows[-1]["content"]), ("interrupted", "保留的正文")
        )

    async def test_server_rejects_client_history_and_skips_empty_answers(self):
        path = await self.new_chat(self.a)
        body = {"request_id": str(uuid4()), "question": "一问"}
        for extra in [{"messages": []}, {"owner_id": str(uuid4())}, {"question": " "}]:
            self.assertEqual(
                (
                    await self.client.post(path, headers=self.a, json=body | extra)
                ).status_code,
                422,
            )
        with patch.object(model, "api_key", SecretStr("")):
            self.assertEqual(
                (await self.client.post(path, headers=self.a, json=body)).status_code,
                503,
            )
        self.assertEqual((await self.client.get(path, headers=self.a)).json(), [])
        inputs = []

        async def upstream(messages, *_):
            inputs.append(messages)
            if len(inputs) == 1:
                raise ModelFailure("模型连接失败")
            yield "回复"

        with patch("chat.stream_reply", upstream):
            await self.client.post(path, headers=self.a, json=body)
            await self.client.post(
                path,
                headers=self.a,
                json={"request_id": str(uuid4()), "question": "二问"},
            )
        self.assertEqual(
            inputs[-1],
            [{"role": "user", "content": "二问"}],
        )

    async def test_abandoned_generation(self):
        path = await self.new_chat(self.a)
        chat_id = UUID(path.split("/")[-2])
        req_id = uuid4()
        async with self.db.begin() as session:
            for pos, role, content, status in [
                (1, "user", "问题", "done"),
                (2, "assistant", "已保存", "streaming"),
            ]:
                session.add(
                    Message(
                        conversation_id=chat_id,
                        position=pos,
                        role=role,
                        request_id=req_id,
                        content=content,
                        status=status,
                        lease_until=datetime.now(UTC) - timedelta(seconds=1),
                    )
                )
        await self.engine.dispose()
        recovered = (await self.client.get(path, headers=self.a)).json()
        self.assertEqual(
            (recovered[1]["status"], recovered[1]["content"]), ("interrupted", "已保存")
        )

        async def upstream(*_):
            yield "恢复成功"

        with patch("chat.stream_reply", upstream):
            reply = await self.client.post(
                path,
                headers=self.a,
                json={"question": "继续", "request_id": str(uuid4())},
            )
            self.assertIn("event: done", reply.text)


@unittest.skipUnless(
    os.getenv("NABUCITE_TEST_POSTGRES") == "1",
    "需独立 PostgreSQL 实例，设置 NABUCITE_TEST_POSTGRES=1",
)
class PostgresPrivateChats(PrivateChats):
    postgres = True

    async def test_simultaneous_admission(self):
        path = f"/api/conversations/{uuid4()}/messages"
        resume = asyncio.Event()
        calls = []

        async def upstream(*_):
            calls.append(1)
            await resume.wait()
            yield "完成"

        body = {
            "first_in": str(SHARED),
            "question": "同时发送",
            "request_id": str(uuid4()),
        }
        with patch("chat.stream_reply", upstream):
            tasks = [
                asyncio.create_task(self.client.post(path, json=body, headers=self.a))
                for _ in range(2)
            ]
            try:
                first, _ = await asyncio.wait(
                    tasks, timeout=10, return_when=asyncio.FIRST_COMPLETED
                )
                self.assertEqual([t.result().status_code for t in first], [409])
            finally:
                resume.set()
                results = await asyncio.gather(*tasks)
            self.assertEqual(sorted(r.status_code for r in results), [200, 409])
            self.assertEqual(len(calls), 1)
            self.assertEqual(
                len((await self.client.get(path, headers=self.a)).json()), 2
            )
