import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from datetime import UTC, datetime, timedelta
from functools import partial
from time import monotonic
from typing import Annotated, Literal
from uuid import UUID, uuid4

from anyio import CancelScope
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from access import membership
from auth import Identity
from context import (
    Step,
    prepare,
    validate_answer,
)
from database import Conversation, Database, Message, ModelVersion, Publication, User
from model_stream import ModelFailure, stream_reply
from models import key_for, metered

router = APIRouter(prefix="/api/conversations")
tasks = {}
LEASE = timedelta(seconds=120)


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["general", "knowledge"] = "general"
    regenerate: UUID | None = None
    first_in: UUID | None = None
    trace: bool = False
    request_id: UUID
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


async def private_chat(session, owner_id, chat_id, *, write=False):
    # 锁住私人会话保护并发入场，再按当前有效授权检查库访问。
    query = (
        select(Conversation)
        .where(
            Conversation.id == chat_id,
            Conversation.owner_id == owner_id,
        )
        .with_for_update(of=Conversation, read=not write)
    )
    chat = await session.scalar(query)
    if chat is None:
        raise HTTPException(404, "会话不存在或不可访问。")
    await membership(session, owner_id, chat.knowledge_base_id)
    return chat


async def expire_abandoned(session, chat_id):
    await session.execute(
        update(Message)
        .where(
            Message.conversation_id == chat_id,
            Message.status == "streaming",
            Message.lease_until <= datetime.now(UTC),
        )
        .values(
            status="interrupted",
            lease_until=None,
            error="生成进程已停止或超时，可继续提问。",
        )
    )


def ordered(chat_id):
    return (
        select(Message)
        .where(Message.conversation_id == chat_id)
        .order_by(Message.position)
    )


@router.get("/{chat_id}/messages")
async def history(chat_id: UUID, user: Identity, db: Database):
    async with db.begin() as session:
        await private_chat(session, user.id, chat_id)
        await expire_abandoned(session, chat_id)
        rows = await session.scalars(ordered(chat_id))
        return [
            {
                "id": m.id,
                "position": m.position,
                "role": m.role,
                "content": m.content,
                "status": "withdrawn" if m.withdrawn else m.status,
                "reply_to": m.reply_to,
                "created_at": stamp(m.created_at),
                "error": m.error,
                "citations": m.citations,
                "progress": m.progress,
                "mode": m.mode,
                "model_version_id": m.model_version_id,
                "request_id": m.request_id,
            }
            for m in rows
        ]


def stamp(value):
    return value.replace(tzinfo=UTC).isoformat() if value else None


async def generate(
    db,
    chat,
    user_id,
    question,
    answer,
    history,
    version,
    key,
    token_version,
    trace,
) -> AsyncIterator[str]:
    def event(name, **values):
        body = {"conversation_id": str(chat.id), "answer_id": str(answer.id), **values}
        return f"event: {name}\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"

    active = update(Message).where(
        Message.id == answer.id, Message.status == "streaming"
    )

    async def persist(**values):
        async with db.begin() as session:
            principal = await session.get(User, user_id)
            if not principal.enabled or principal.token_version != token_version:
                raise HTTPException(401, "账号登录已失效。")
            await private_chat(session, user_id, chat.id)
            result = await session.execute(
                active.where(Message.lease_until > datetime.now(UTC)).values(**values)
            )
            if result.rowcount != 1:
                raise ModelFailure("本轮生成已中断，请重新读取会话。")

    seq = 0
    running = None
    finishing = None
    steps = []

    clocks = {}

    def record(stage, status):
        nonlocal seq, running
        seq += 1
        running = stage if status == "active" else None
        if status == "active" or stage not in clocks:
            clocks[stage] = (seq, monotonic())
        idx, began_at = clocks[stage]
        step = {
            "stage": stage,
            "status": status,
            "seq": seq,
            "id": f"{stage}-{idx}",
            "offset_ms": round((monotonic() - began) * 1000),
            "elapsed_ms": round((monotonic() - began_at) * 1000),
        }
        if stage in ("retrieve", "read"):
            step["tool"] = "search_knowledge" if stage == "retrieve" else "read_passage"
        steps[:] = [s for s in steps if s["id"] != step["id"]] + [step]
        return event("progress", **step)

    async def progress(stage, status):
        frame = record(stage, status)
        await persist(progress=steps, lease_until=datetime.now(UTC) + LEASE)
        return frame

    from telemetry import DURATION

    began = monotonic()
    first_delta = True
    audit = None
    finished = False
    reason = "客户端连接中断，可继续提问。"
    failed_step = None
    try:
        from vector import turn_account

        turn_account.set((db, user_id, answer.id, version.id))
        tasks[answer.id] = asyncio.current_task()
        await persist(lease_until=datetime.now(UTC) + LEASE)
        yield event(
            "start",
            user_message_id=str(question.id),
            user_time=stamp(question.created_at),
            answer_time=stamp(answer.created_at),
        )

        async def call(purpose, messages, limit=None, tools=None, compatibility=False):
            caller = stream_reply
            if compatibility and tools:
                caller = partial(compatible_stream, stream_reply)
            elif tools:
                caller = partial(stream_reply, tools=tools)
            renewal = monotonic() + LEASE.total_seconds() / 3
            await persist(lease_until=datetime.now(UTC) + LEASE)
            async with aclosing(
                metered(
                    db,
                    user_id,
                    answer.id,
                    version,
                    key,
                    purpose,
                    messages,
                    caller,
                    limit,
                )
            ) as upstream:
                async for part in upstream:
                    if monotonic() >= renewal:
                        renewal = monotonic() + LEASE.total_seconds() / 3
                        await persist(lease_until=datetime.now(UTC) + LEASE)
                    yield part

        from knowledge_turn import compatible_stream, respond

        sources, hits, citations = [], [], []
        timing = {"elapsed": lambda: round((monotonic() - began) * 1000)}

        async def general():
            yield Step("wait", "active")
            messages = await prepare(db, chat, question, history, version, call)
            async with aclosing(call("answer", messages)) as stream:
                first = True
                async for part in stream:
                    if first:
                        timing["upstream_first_ms"] = timing["elapsed"]()
                        yield Step("wait", "complete")
                        yield Step("answer", "active")
                        first = False
                    yield part

        stream = (
            respond(db, chat, question, history, version, call, sources, hits, timing)
            if question.mode == "knowledge"
            else general()
        )
        async with aclosing(stream) as parts:
            async for part in parts:
                if isinstance(part, Step):
                    yield await progress(*part)
                    continue
                if sources and "[" in part:
                    citations.extend(
                        c for c in validate_answer(part, sources) if c not in citations
                    )
                await persist(
                    content=Message.content + part,
                    citations=citations,
                    lease_until=datetime.now(UTC) + LEASE,
                )
                if first_delta:
                    timing["visible_delta_ms"] = timing["elapsed"]()
                    DURATION.labels("first_delta").observe(monotonic() - began)
                    first_delta = False
                yield event("delta", text=part, citations=citations)
        if trace:
            audit = {k: v for k, v in timing.items() if k != "elapsed"}
            audit["hits"] = [
                {
                    k: h.get(k)
                    for k in (
                        "document_id",
                        "chunk_id",
                        "page_start",
                        "page_end",
                        "score",
                    )
                }
                for h in hits
            ]
        if trace:
            from database import Usage

            async with db() as session:
                await private_chat(session, user_id, chat.id)
                calls = await session.scalars(
                    select(Usage).where(Usage.answer_id == answer.id)
                )
                audit["usage"] = [
                    {
                        "call_id": str(r.id),
                        "purpose": r.purpose,
                        "source": r.source,
                        "input": r.input_tokens,
                        "output": r.output_tokens,
                    }
                    for r in calls
                ]
        finishing = running
        final_step = record(finishing, "complete") if finishing else None
        await persist(status="done", lease_until=None, progress=steps)
        finished = True
        if final_step:
            yield final_step
        yield event("done", citations=citations, **({"trace": audit} if trace else {}))
    except ModelFailure as exc:
        reason = str(exc)
    except HTTPException:
        audit = None
        reason = "会话已不可访问。"
    except SQLAlchemyError:
        reason = "回答保存失败，请刷新查看已保存的内容。"
    finally:
        tasks.pop(answer.id, None)
        if not finished:
            # 客户端取消时也结束本轮。进程被强杀或数据库离线时由租约接管。
            unfinished = running or finishing
            failed_step = record(unfinished, "failed") if unfinished else None
            with CancelScope(shield=True):
                try:
                    async with db.begin() as session:
                        await session.execute(
                            active.values(
                                status="interrupted",
                                lease_until=None,
                                error=reason,
                                progress=steps,
                            )
                        )
                except SQLAlchemyError:
                    reason = "数据库暂不可用，请恢复连接后刷新会话。"
    if not finished:
        if audit is not None:
            try:
                async with db() as session:
                    await private_chat(session, user_id, chat.id)
                    principal = await session.get(User, user_id)
                    if (
                        not principal.enabled
                        or not principal.system_admin
                        or principal.token_version != token_version
                    ):
                        audit = None
            except (HTTPException, SQLAlchemyError):
                audit = None
        if failed_step:
            yield failed_step
        yield event(
            "error", message=reason, **({"trace": audit} if audit is not None else {})
        )


async def admit(chat_id, body, user, db, withdraw=False):
    async with db.begin() as session:
        if body.first_in:
            await membership(session, user.id, body.first_in)
            # 草稿 UUID 与首轮共享一个事务，重试在同一个会话锁上对账。
            await session.execute(
                insert(Conversation)
                .values(id=chat_id, knowledge_base_id=body.first_in, owner_id=user.id)
                .on_conflict_do_nothing(index_elements=[Conversation.id])
            )
        chat = await private_chat(session, user.id, chat_id, write=True)
        if body.first_in and chat.knowledge_base_id != body.first_in:
            raise HTTPException(404, "会话不存在或不可访问。")
        await expire_abandoned(session, chat_id)
        rows = (await session.scalars(ordered(chat_id))).all()
        existing = next(
            (
                m
                for m in rows
                if m.request_id == body.request_id and m.role == "assistant"
            ),
            None,
        )
        if existing and withdraw:
            if existing.withdrawn:
                return existing.id
            if existing.reply_to is None:
                existing.reply_to = next(
                    (
                        m.id
                        for m in rows
                        if m.role == "user" and m.request_id == existing.request_id
                    ),
                    None,
                )
            existing.withdrawn = True
            existing.status = "interrupted"
            existing.content, existing.citations, existing.progress = "", [], []
            existing.error, existing.lease_until = None, None
            chat.summary, chat.summary_position = "", 0
            return existing.id
        if any(m.request_id == body.request_id for m in rows):
            raise HTTPException(
                409,
                {
                    "code": "request_exists",
                    "message": "此请求已被接收，请查看已保存的消息。",
                },
            )
        if body.first_in and rows:
            raise HTTPException(409, "该草稿已转为正式会话，请打开历史记录。")
        if any(m.status == "streaming" for m in rows):
            raise HTTPException(409, "该会话正在生成回答，请稍后再发送。")
        if chat.archived:
            raise HTTPException(409, "请恢复会话后再发送。")
        publication = await session.get(Publication, 1)
        if not publication:
            raise HTTPException(503, "请先运行初始化命令。")
        version = await session.get(ModelVersion, publication.version_id)
        key = None if withdraw else key_for(version)
        pos = rows[-1].position + 1 if rows else 1
        now = datetime.now(UTC)
        common = {
            "conversation_id": chat.id,
            "request_id": body.request_id,
            "mode": body.mode,
            "model_version_id": version.id,
        }
        if body.regenerate:
            question = next(
                (m for m in rows if m.id == body.regenerate and m.role == "user"), None
            )
            attempts = [
                m
                for m in rows
                if question
                and m.role == "assistant"
                and (m.reply_to == question.id or m.request_id == question.request_id)
            ]
            if (
                not attempts
                or not attempts[-1].withdrawn
                or question.content != body.question
                or question.mode != body.mode
            ):
                raise HTTPException(409, "只能重新生成已撤回问题的回答。")
        else:
            question = Message(
                **common,
                id=uuid4(),
                position=pos,
                role="user",
                content=body.question,
                status="done",
                created_at=now,
            )
            session.add(question)
            pos += 1
        answer = Message(
            **common,
            id=uuid4(),
            position=pos,
            role="assistant",
            content="",
            reply_to=question.id,
            status="interrupted" if withdraw else "streaming",
            withdrawn=withdraw,
            created_at=now,
            lease_until=None if withdraw else now + LEASE,
        )
        session.add(answer)
        chat.updated_at = now
        if not rows:
            chat.title = body.question[:100]
    if withdraw:
        return answer.id
    return StreamingResponse(
        generate(
            db,
            chat,
            user.id,
            question,
            answer,
            rows,
            version,
            key,
            user.token_version,
            body.trace and user.system_admin,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/{chat_id}/messages", response_class=StreamingResponse)
async def send(chat_id: UUID, body: Question, user: Identity, db: Database):
    return await admit(chat_id, body, user, db)


@router.post("/{chat_id}/withdraw")
async def withdraw(chat_id: UUID, body: Question, user: Identity, db: Database):
    answer = await admit(chat_id, body, user, db, withdraw=True)
    task = tasks.pop(answer, None)
    if task and not task.done():
        task.cancel()
    return {"answer_id": answer, "status": "withdrawn"}


class ChatEdit(BaseModel):
    title: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
        ]
        | None
    ) = None
    archived: bool | None = None


@router.patch("/{chat_id}")
async def edit(chat_id: UUID, body: ChatEdit, user: Identity, db: Database):
    async with db.begin() as session:
        chat = await private_chat(session, user.id, chat_id, write=True)
        if body.archived is not None:
            await expire_abandoned(session, chat_id)
            if body.archived and await session.scalar(
                select(Message.id).where(
                    Message.conversation_id == chat_id, Message.status == "streaming"
                )
            ):
                raise HTTPException(409, "请等待当前回答完成后再归档。")
            chat.archived = body.archived
        if body.title is not None:
            chat.title = body.title
        return {"id": chat.id, "title": chat.title, "archived": chat.archived}
