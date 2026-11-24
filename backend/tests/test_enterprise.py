import asyncio
import json
import os
import unittest
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select
from test_private_chats import ChatFixture

from access import DEFAULT_DEPARTMENT
from context import INSUFFICIENT, validate_answer
from database import (
    Chunk,
    Conversation,
    DepartmentMember,
    Document,
    Message,
    ProjectMember,
    Publication,
    Usage,
    User,
)
from model_stream import ModelFailure, ToolCall
from seed import RESTRICTED, SHARED


class Enterprise(ChatFixture):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.db.begin() as session:
            users = (await session.scalars(select(User).order_by(User.username))).all()
            self.uid, self.bid = [u.id for u in users]
            users[0].system_admin = True

    async def test_local_turn_and_archive(self):
        chat = await self.new_chat(self.b)
        answers = iter(
            [
                {"kind": "social", "text": "见到你真好。"},
                {"kind": "social", "text": "很高兴帮上忙。"},
                {"kind": "clarify", "text": "你指哪项支出？"},
            ]
        )

        async def reply(*args, **kwargs):
            yield json.dumps(next(answers), ensure_ascii=False)

        with (
            patch("knowledge_turn.retrieve") as retrieve,
            patch("chat.stream_reply", reply),
        ):
            for question in ["久违了，近来可好？", "承蒙关照", "这笔如何处理？"]:
                response = await self.client.post(
                    chat,
                    headers=self.b,
                    json={
                        "mode": "knowledge",
                        "question": question,
                        "request_id": str(uuid4()),
                    },
                )
                self.assertIn("event: done", response.text)
                self.assertNotIn('"stage": "retrieve"', response.text)
            retrieve.assert_not_called()
        self.assertEqual(len((await self.client.get(chat, headers=self.b)).json()), 6)
        path = "/api/admin/libraries/" + str(SHARED)
        self.assertEqual(
            (
                await self.client.patch(path, headers=self.a, json={"deleted": True})
            ).status_code,
            200,
        )
        self.assertEqual((await self.client.get(chat, headers=self.b)).status_code, 404)
        await self.client.patch(path, headers=self.a, json={"deleted": False})
        self.assertEqual((await self.client.get(chat, headers=self.b)).status_code, 200)
        from vector import fuse

        hits = [{"_id": "a"}, {"_id": "b"}]
        self.assertEqual([h["_id"] for h in fuse(hits, [hits[1], hits[1]])], ["b", "a"])

    async def test_batch_scope_and_project_retry(self):
        project = (
            await self.client.post(
                "/api/admin/organizations",
                headers=self.a,
                json={"kind": "project", "name": "临时项目"},
            )
        ).json()["id"]
        path = "/api/admin/employees/batch"
        join = {"ids": [str(self.bid)], "action": "join", "org_id": project}
        self.assertEqual(
            (await self.client.post(path, headers=self.b, json=join)).status_code, 403
        )
        for _ in range(2):
            self.assertEqual(
                (await self.client.post(path, headers=self.a, json=join)).json()[
                    "success"
                ],
                [str(self.bid)],
            )
        async with self.db() as session:
            self.assertEqual(
                await session.scalar(
                    select(func.count())
                    .select_from(ProjectMember)
                    .where(ProjectMember.org_id == UUID(project))
                ),
                1,
            )
        result = (
            await self.client.post(
                path,
                headers=self.a,
                json={"action": "disable", "ids": [str(self.uid), str(self.bid)]},
            )
        ).json()
        self.assertEqual(result["success"], [str(self.bid)])
        self.assertIn(str(self.uid), result["failed"])

    async def test_usage_local_midnight_and_unknown_counts(self):
        async with self.db.begin() as session:
            version = (await session.get(Publication, 1)).version_id
            for day, hour, minute, source in [
                (8, 15, 59, "reported"),
                (8, 16, 0, "unknown"),
                (9, 15, 59, "reported"),
                (9, 16, 0, "estimated"),
            ]:
                session.add(
                    Usage(
                        user_id=self.uid,
                        department_id=DEFAULT_DEPARTMENT,
                        version_id=version,
                        purpose="verify",
                        source=source,
                        input_tokens=7 if source != "unknown" else None,
                        output_tokens=2 if source != "unknown" else None,
                        created_at=datetime(2024, 1, day, hour, minute, tzinfo=UTC),
                    )
                )
        result = await self.client.get(
            "/api/usage?start=2024-01-09&end=2024-01-09", headers=self.a
        )
        rows = {row["source"]: row for row in result.json()}
        self.assertEqual(set(rows), {"reported", "unknown"})
        self.assertEqual(rows["reported"]["calls"], 1)
        self.assertIsNone(rows["unknown"]["input"])
        self.assertIsNone(rows["unknown"]["output"])

    async def test_access_sources_password_and_private_ownership(self):
        async def post_org(kind, name):
            r = await self.client.post(
                "/api/admin/organizations",
                headers=self.a,
                json={"kind": kind, "name": name},
            )
            self.assertEqual(r.status_code, 201)
            return r.json()["id"]

        project = await post_org("project", "测试项目")
        department = await post_org("department", "测试部门")
        path = f"/api/admin/employees/{self.bid}"
        body = {
            "username": "demo_b",
            "display_name": "同事乙",
            "department_id": department,
            "projects": [project],
        }
        self.assertEqual(
            (await self.client.put(path, headers=self.b, json=body)).status_code, 403
        )
        self.assertEqual(
            (await self.client.put(path, headers=self.a, json=body)).status_code, 200
        )
        base = f"/api/knowledge-bases/{RESTRICTED}"

        async def grant(subject, role):
            r = await self.client.put(
                f"/api/admin/grants/organization/{subject}/{RESTRICTED}",
                headers=self.a,
                json={"role": role},
            )
            self.assertEqual(r.status_code, 200)

        await grant(project, "admin")
        await grant(department, "reader")
        info = (await self.client.get(base, headers=self.b)).json()
        self.assertEqual((info["role"], len(info["sources"])), ("admin", 2))
        self.assertEqual(
            (
                await self.client.post(
                    base + "/documents",
                    headers=self.b
                    | {"Content-Type": "application/pdf", "X-Filename": "test.pdf"},
                    content=b"%PDF-",
                )
            ).status_code,
            403,
        )
        chat = (await self.client.post(base + "/conversations", headers=self.b)).json()[
            "id"
        ]
        private = f"/api/conversations/{chat}/messages"
        self.assertEqual(
            (await self.client.get(private, headers=self.a)).status_code, 404
        )
        await self.client.put(path, headers=self.a, json=body | {"projects": []})
        self.assertEqual(
            (await self.client.get(base, headers=self.b)).json()["role"], "reader"
        )
        self.assertEqual(
            (await self.client.get(private, headers=self.b)).status_code, 200
        )
        await grant(department, None)
        self.assertEqual(
            (await self.client.get(private, headers=self.b)).status_code, 404
        )
        profile = "/api/users/me"
        self.assertEqual(
            (
                await self.client.patch(
                    profile,
                    headers=self.b,
                    json={"username": "demo_a", "current_password": self.password},
                )
            ).status_code,
            409,
        )
        self.assertEqual(
            (
                await self.client.patch(
                    profile,
                    headers=self.b,
                    json={
                        "password": "passwordpassword",
                        "current_password": self.password,
                    },
                )
            ).status_code,
            422,
        )
        self.assertEqual(
            (
                await self.client.patch(
                    profile, headers=self.b, json={"username": "changed"}
                )
            ).status_code,
            403,
        )
        password = "long test passphrase with spaces " * 3
        r = await self.client.patch(
            profile,
            headers=self.b,
            json={
                "display_name": "测试",
                "username": "changed",
                "password": password,
                "current_password": self.password,
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            (await self.client.get(profile, headers=self.b)).status_code, 401
        )
        r = await self.client.post(
            "/api/auth/login", json={"username": "changed", "password": password}
        )
        self.assertEqual(r.status_code, 200)
        fresh = {"Authorization": "Bearer " + r.json()["access_token"]}
        self.assertEqual(
            (await self.client.get(profile, headers=fresh)).json()["id"], str(self.bid)
        )
        await self.client.put(path, headers=self.a, json=body | {"enabled": False})
        self.assertEqual(
            (await self.client.get(profile, headers=fresh)).status_code, 401
        )

    async def test_document_boundaries_and_retry(self):
        base = f"/api/knowledge-bases/{RESTRICTED}/documents"
        with (
            patch("documents.put_document") as store,
            patch("documents.enqueue") as enqueue,
        ):
            upload = {
                "content": b"%PDF-test-envelope",
                "headers": self.b | {"X-Filename": "test.pdf"},
            }
            self.assertEqual((await self.client.post(base, **upload)).status_code, 404)
            self.assertEqual(
                (
                    await self.client.post(self.library + "/documents", **upload)
                ).status_code,
                403,
            )
            store.assert_not_called()
            r = await self.client.post(
                base,
                content=b"%PDF-test-envelope",
                headers=self.a | {"X-Filename": "test.pdf"},
            )
            self.assertEqual(r.status_code, 202)
            doc = UUID(r.json()["id"])
            store.assert_called_once()
            async with self.db.begin() as session:
                source = await session.get(Document, doc)
                attempt = source.attempt
                source.status = "failed"
            endpoint = f"{base}/{doc}"
            self.assertEqual(
                (
                    await self.client.post(endpoint + "/retry", headers=self.b)
                ).status_code,
                404,
            )
            self.assertEqual(
                (
                    await self.client.post(endpoint + "/retry", headers=self.a)
                ).status_code,
                202,
            )
            self.assertEqual(
                (
                    await self.client.post(endpoint + "/retry", headers=self.a)
                ).status_code,
                409,
            )
            self.assertEqual(enqueue.await_count, 2)
            chunk = uuid4()
            async with self.db.begin() as session:
                source = await session.get(Document, doc)
                self.assertNotEqual(source.attempt, attempt)
                source.status = "ready"
                session.add(
                    Chunk(
                        id=chunk,
                        document_id=doc,
                        text="受限测试片段",
                        section="测试",
                        page_start=1,
                        page_end=1,
                    )
                )
            for path in (base, endpoint + "/original", endpoint + f"/chunks/{chunk}"):
                self.assertEqual(
                    (await self.client.get(path, headers=self.b)).status_code, 404
                )
            self.assertEqual(
                (
                    await self.client.get(endpoint + f"/chunks/{chunk}", headers=self.a)
                ).json()["text"],
                "受限测试片段",
            )
            self.assertEqual(
                (
                    await self.client.get(
                        self.library + f"/documents/{doc}/chunks/{chunk}",
                        headers=self.b,
                    )
                ).status_code,
                404,
            )

    async def test_passage_scope_is_checked_on_each_read(self):
        from types import SimpleNamespace

        from fastapi import HTTPException

        from knowledge_turn import read_source

        document_id, passage_id = uuid4(), uuid4()
        async with self.db.begin() as session:
            session.add(
                Document(
                    id=document_id,
                    knowledge_base_id=SHARED,
                    status="ready",
                    filename="测试.pdf",
                    object_key=str(document_id),
                )
            )
            await session.flush()
            for cid, body in [
                (passage_id, "费用上限见表"),
                (uuid4(), "表头及适用条件"),
            ]:
                session.add(
                    Chunk(
                        id=cid,
                        document_id=document_id,
                        section="测试",
                        page_start=2,
                        page_end=2,
                        text=body,
                    )
                )
        chat = SimpleNamespace(owner_id=self.bid, knowledge_base_id=SHARED)
        ref = {"document_id": str(document_id), "chunk_id": str(passage_id)}
        rows = await read_source(self.db, chat, ref)
        self.assertEqual([r["text"] for r in rows], ["费用上限见表", "表头及适用条件"])
        with self.assertRaises(HTTPException):
            await read_source(
                self.db,
                SimpleNamespace(owner_id=self.bid, knowledge_base_id=RESTRICTED),
                ref,
            )
        async with self.db.begin() as session:
            (await session.get(Document, document_id)).status = "failed"
        with self.assertRaises(ModelFailure):
            await read_source(self.db, chat, ref)

    async def test_structural_read_across_page_boundary(self):
        from pathlib import Path
        from types import SimpleNamespace

        from ingestion import parse_document
        from knowledge_turn import read_source

        doc_id = uuid4()
        parent = uuid4()
        sample = [
            Chunk(
                id=uuid4(),
                document_id=doc_id,
                section="A",
                parent_id=parent,
                page_start=page,
                page_end=page,
                text=body,
            )
            for page, body in [(1, "适用条件"), (2, "参数值"), (2, "附注")]
        ]
        sample.append(
            Chunk(
                id=uuid4(),
                document_id=doc_id,
                section="B",
                page_start=2,
                page_end=2,
                text="其他章节",
            )
        )
        with patch("ingestion.pdf_chunks", return_value=sample):
            chunks = parse_document(Path("example.pdf"), doc_id)
        self.assertEqual([c.ordinal for c in chunks], list(range(4)))
        self.assertNotEqual(chunks[2].parent_id, chunks[3].parent_id)
        async with self.db.begin() as session:
            session.add(
                Document(
                    id=doc_id,
                    knowledge_base_id=SHARED,
                    status="ready",
                    filename="example.pdf",
                    object_key=str(doc_id),
                )
            )
            await session.flush()
            session.add_all(chunks)
        rows = await read_source(
            self.db,
            SimpleNamespace(owner_id=self.bid, knowledge_base_id=SHARED),
            {"document_id": str(doc_id), "chunk_id": str(chunks[1].id)},
        )
        self.assertEqual([r["text"] for r in rows], ["参数值", "适用条件", "附注"])

    async def test_search_filters_and_reauthorization(self):
        from fastapi import HTTPException

        from search import retrieve
        from vector import COLLECTION

        ready, failed, restricted = uuid4(), uuid4(), uuid4()
        good_chunk, other_chunk = uuid4(), uuid4()
        async with self.db.begin() as session:
            for doc_id, kb, status in [
                (ready, SHARED, "ready"),
                (failed, SHARED, "failed"),
                (restricted, RESTRICTED, "ready"),
            ]:
                session.add(
                    Document(
                        id=doc_id,
                        knowledge_base_id=kb,
                        status=status,
                        filename="范围测试.pdf",
                        vector_versions=[COLLECTION],
                        object_key=str(doc_id),
                    )
                )
            await session.flush()
            for chunk, doc in ((good_chunk, ready), (other_chunk, restricted)):
                session.add(
                    Chunk(
                        id=chunk,
                        document_id=doc,
                        section="测试",
                        page_start=1,
                        page_end=1,
                        text="受限库登记办法" if doc == restricted else "公开登记办法",
                    )
                )

        def search(request):
            criteria = json.loads(request.content)["query"]["bool"]
            self.assertIn({"terms": {"document_id": [str(ready)]}}, criteria["filter"])
            self.assertIn(
                {"term": {"knowledge_base_id": str(SHARED)}}, criteria["filter"]
            )
            return httpx.Response(
                200,
                json={
                    "hits": {
                        "hits": [
                            {"_id": str(cid), "_score": 1.0}
                            for cid in (good_chunk, other_chunk)
                        ]
                    }
                },
            )

        def vector_search(**kwargs):
            self.assertIn(f'kb == "{SHARED}"', kwargs["filter"])
            return [
                [
                    {"entity": {"chunk": str(cid)}, "distance": 1.0}
                    for cid in (other_chunk, good_chunk)
                ]
            ]

        def gateway(path, body):
            self.assertEqual(body["input"]["documents"], ["测试\n公开登记办法"])
            return {"output": {"results": [{"index": 0, "relevance_score": 1.0}]}}

        for strategy in ("bm25", "vector", "hybrid", "rerank"):
            with (
                self.subTest(strategy=strategy),
                patch.dict(os.environ, {"SEARCH_PASSWORD": "test-only"}),
                patch(
                    "search.httpx.AsyncClient",
                    return_value=httpx.AsyncClient(
                        transport=httpx.MockTransport(search)
                    ),
                ) as lexical,
                patch("vector.client") as vector_client,
                patch("vector.gateway", side_effect=gateway) as reranker,
            ):
                vector_client.return_value.search.side_effect = vector_search
                with self.assertRaises(HTTPException) as err:
                    await retrieve(
                        self.db, self.bid, RESTRICTED, "登记", strategy, [1.0]
                    )
                self.assertEqual(err.exception.status_code, 404)
                lexical.assert_not_called()
                vector_client.assert_not_called()
                reranker.assert_not_called()
                results = await retrieve(
                    self.db, self.bid, SHARED, "登记", strategy, [1.0]
                )
                self.assertEqual(
                    [row["chunk_id"] for row in results], [str(good_chunk)]
                )
                self.assertEqual(reranker.await_count, int(strategy == "rerank"))

    async def test_citations_followup_usage_and_archive(self):
        path = await self.new_chat(self.a)
        doc, chunk = uuid4(), uuid4()
        source = {
            "document_id": str(doc),
            "chunk_id": str(chunk),
            "knowledge_base_id": str(SHARED),
            "page_start": 3,
            "page_end": 3,
            "filename": "隔离样本.pdf",
            "section": "办公",
            "text": "借用设备请登记。",
            "score": 1.7,
        }

        async def ask(q):
            return await self.client.post(
                path,
                headers=self.a,
                json={
                    "request_id": str(uuid4()),
                    "question": q,
                    "mode": "knowledge",
                    "trace": True,
                },
            )

        async def upstream(messages, base, name, key, limit, usage, **kwargs):
            usage.update(prompt_tokens=37, completion_tokens=12)
            if messages[-1]["role"] == "user":
                yield ToolCall("scope", "search_knowledge", '{"query":"设备借用 登记"}')
            elif "无可用证据" in messages[-1]["content"]:
                yield INSUFFICIENT
            else:
                yield "设备借用需要登记 [1]。"

        with patch("chat.stream_reply", upstream):
            self.assertIn("没有可用文档", (await ask("劳驾，请教设备手续")).text)
        with (
            patch("knowledge_turn.retrieve", return_value=[source]) as search,
            patch("chat.stream_reply", upstream),
        ):
            result = await ask("怎样借用设备")
            self.assertIn("event: done", result.text)
            second = await ask("那这个呢")
            self.assertIn("reported", second.text)
            self.assertEqual(search.await_args.args[-1], "设备借用 登记")
            with patch("knowledge_turn.retrieve", return_value=[]):
                self.assertIn(INSUFFICIENT, (await ask("无匹配")).text)
        history = (await self.client.get(path, headers=self.a)).json()
        self.assertEqual(history[-3]["citations"][0]["chunk_id"], str(chunk))
        self.assertEqual(history[-1]["citations"], [])
        chat_path = path.removesuffix("/messages")
        self.assertEqual(
            (
                await self.client.patch(
                    chat_path, headers=self.b, json={"archived": True}
                )
            ).status_code,
            404,
        )
        self.assertEqual(
            (
                await self.client.patch(
                    chat_path,
                    headers=self.a,
                    json={"archived": True, "title": "存档测试"},
                )
            ).status_code,
            200,
        )
        self.assertEqual((await self.client.get(self.chats, headers=self.a)).json(), [])
        self.assertEqual(
            (
                await self.client.get(
                    self.chats + "?archived=true&q=存档", headers=self.a
                )
            ).json()[0]["title"],
            "存档测试",
        )
        self.assertEqual((await ask("继续")).status_code, 409)
        await self.client.patch(chat_path, headers=self.a, json={"archived": False})
        self.assertEqual((await self.client.get(path, headers=self.a)).json(), history)
        stats = (
            "/api/usage?start="
            + str(date.today() - timedelta(days=2))
            + "&end="
            + str(date.today() + timedelta(days=2))
        )
        rows = (await self.client.get(stats, headers=self.a)).json()
        self.assertEqual(sum(r["rounds"] for r in rows), 3)
        self.assertTrue(all(r["source"] == "reported" for r in rows))
        self.assertEqual((await self.client.get(stats, headers=self.b)).json(), [])
        self.assertEqual(
            (
                await self.client.get(stats + "&organization=true", headers=self.b)
            ).status_code,
            403,
        )
        with self.assertRaises(ModelFailure):
            validate_answer("答复 [8]", [{"number": 1}])

    async def test_version_pinning_budget_summary_and_usage_snapshot(self):
        path = await self.new_chat(self.a)
        original = (await self.client.get("/api/admin/models", headers=self.a)).json()[
            0
        ]
        draft = {k: original[k] for k in ("base_url", "model", "credential")}
        resp = await self.client.post(
            "/api/admin/models",
            headers=self.a,
            json=draft | {"window": 1400, "output": 128},
        )
        self.assertEqual(resp.status_code, 201)
        version = resp.json()["id"]
        publish = f"/api/admin/models/{version}/publish"
        entered, resume = asyncio.Event(), asyncio.Event()
        limits = []

        async def upstream(messages, base, model, key, limit, usage):
            limits.append(limit)
            if len(limits) == 1:
                entered.set()
                await resume.wait()
            if messages[0]["role"] == "system":
                yield "主题是设备采购。"
            else:
                yield "设备采购流程。" * 12

        def ask(q):
            return self.client.post(
                path, headers=self.a, json={"request_id": str(uuid4()), "question": q}
            )

        with patch("chat.stream_reply", upstream):
            first = asyncio.create_task(ask("采购设备" + "甲" * 60))
            await asyncio.wait_for(entered.wait(), 5)
            try:
                self.assertEqual(
                    (
                        await self.client.patch(
                            path.removesuffix("/messages"),
                            headers=self.a,
                            json={"archived": True},
                        )
                    ).status_code,
                    409,
                )
                self.assertEqual(
                    (await self.client.post(publish, headers=self.a)).status_code, 200
                )
            finally:
                resume.set()
            self.assertIn("event: done", (await first).text)
            for i in range(5):
                self.assertIn(
                    "event: done", (await ask(str(i) + "的采购流程" + "乙" * 60)).text
                )
            self.assertEqual(limits[0], original["output"])
            self.assertEqual(limits[1], 128)
            self.assertIn("请缩小问题范围", (await ask("长" * 1000)).text)
            self.assertEqual(
                (
                    await self.client.post(
                        f"/api/admin/models/{original['id']}/publish", headers=self.a
                    )
                ).status_code,
                200,
            )
        async with self.db() as session:
            answer = list(
                await session.scalars(
                    select(Message)
                    .where(Message.role == "assistant")
                    .order_by(Message.position)
                )
            )
            self.assertEqual(str(answer[0].model_version_id), original["id"])
            self.assertTrue(all(str(a.model_version_id) == version for a in answer[1:]))
            chat = await session.get(Conversation, answer[0].conversation_id)
            self.assertGreater(chat.summary_position, 0)
            self.assertTrue(chat.summary)
            usage = list(await session.scalars(select(Usage)))
            self.assertTrue(any(u.purpose == "summary" for u in usage))
            self.assertTrue(
                all(
                    u.source == "estimated" and u.department_id == DEFAULT_DEPARTMENT
                    for u in usage
                )
            )
        org = await self.client.post(
            "/api/admin/organizations",
            headers=self.a,
            json={"kind": "department", "name": "新部门"},
        )
        async with self.db.begin() as session:
            (await session.get(DepartmentMember, self.uid)).org_id = UUID(
                org.json()["id"]
            )
        stats = (
            "/api/usage?organization=true&start="
            + str(date.today() - timedelta(days=2))
            + "&end="
            + str(date.today() + timedelta(days=2))
        )
        self.assertEqual(
            (
                await self.client.get(
                    stats + "&department_id=" + org.json()["id"], headers=self.a
                )
            ).json(),
            [],
        )


@unittest.skipUnless(os.getenv("NABUCITE_TEST_POSTGRES") == "1", "需要独立 PostgreSQL")
class PostgresEnterprise(Enterprise):
    postgres = True

    async def test_worker_failure_redelivery_and_idempotent_publication(self):
        # 使用真实数据库、任务锁和发布事务，隔离需要独立服务的三个 I/O 边界。
        with patch.dict(os.environ, {"RABBIT_PASSWORD": "ephemeral-test-fixture"}):
            import ingestion
        doc_id, attempt, chunk_id = uuid4(), uuid4(), uuid4()
        async with self.db.begin() as session:
            session.add(
                Document(
                    id=doc_id,
                    knowledge_base_id=SHARED,
                    filename="isolated.pdf",
                    object_key=str(doc_id),
                    attempt=attempt,
                )
            )

        def parse(*args):
            return [
                Chunk(
                    id=chunk_id,
                    document_id=doc_id,
                    text="设备登记",
                    section="测试",
                    page_start=1,
                    page_end=1,
                )
            ]

        with (
            patch("ingestion.create_async_engine", return_value=self.engine),
            patch("ingestion.objects"),
            patch("ingestion.parse_document", side_effect=parse),
            patch("ingestion.index_chunks", side_effect=ValueError("片段索引失败。")),
        ):
            await ingestion.process(doc_id, attempt)
        async with self.db() as session:
            self.assertEqual((await session.get(Document, doc_id)).status, "failed")
            self.assertEqual(
                await session.scalar(select(func.count()).select_from(Chunk)), 0
            )
        with patch("documents.enqueue"):
            self.assertEqual(
                (
                    await self.client.post(
                        self.library + f"/documents/{doc_id}/retry", headers=self.a
                    )
                ).status_code,
                202,
            )
        async with self.db() as session:
            new_attempt = (await session.get(Document, doc_id)).attempt
        with (
            patch("ingestion.create_async_engine", return_value=self.engine),
            patch("ingestion.objects"),
            patch("ingestion.parse_document", side_effect=parse),
            patch("ingestion.index_chunks") as index,
        ):
            await ingestion.process(doc_id, attempt)
            await ingestion.process(doc_id, new_attempt)
            await ingestion.process(doc_id, new_attempt)
            index.assert_awaited_once()
        async with self.db() as session:
            self.assertEqual((await session.get(Document, doc_id)).status, "ready")
            self.assertEqual(
                await session.scalar(select(func.count()).select_from(Chunk)), 1
            )
