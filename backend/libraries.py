from typing import Annotated
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select

from access import authorized_libraries, membership
from auth import Identity
from database import Conversation, Database, KnowledgeBase

router = APIRouter(prefix="/api/knowledge-bases")


class Rename(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)
    ]


@router.get("")
async def libraries(user: Identity, db: Database):
    async with db() as session:
        return await authorized_libraries(session, user.id)


@router.get("/{kb_id}")
async def library(kb_id: UUID, user: Identity, db: Database):
    async with db() as session:
        return await membership(session, user.id, kb_id)


@router.patch("/{kb_id}")
async def rename(kb_id: UUID, body: Rename, user: Identity, db: Database):
    async with db.begin() as session:
        view = await membership(session, user.id, kb_id, admin=True)
        kb = await session.get(KnowledgeBase, kb_id)
        kb.name = body.name
        return view | {"name": kb.name}


def chat_view(chat):
    return {
        "id": chat.id,
        "knowledge_base_id": chat.knowledge_base_id,
        "title": chat.title,
        "updated_at": chat.updated_at,
        "archived": chat.archived,
    }


@router.post("/{kb_id}/conversations", status_code=201)
async def create(kb_id: UUID, user: Identity, db: Database):
    async with db.begin() as session:
        await membership(session, user.id, kb_id)
        chat = Conversation(owner_id=user.id, knowledge_base_id=kb_id)
        session.add(chat)
        await session.flush()
        return chat_view(chat)


@router.get("/{kb_id}/conversations")
async def conversations(
    kb_id: UUID, user: Identity, db: Database, archived: bool = False, q: str = ""
):
    async with db() as session:
        await membership(session, user.id, kb_id)
        chats = await session.scalars(
            select(Conversation)
            .where(
                Conversation.owner_id == user.id,
                Conversation.archived == archived,
                Conversation.title.contains(q, autoescape=True),
                Conversation.knowledge_base_id == kb_id,
            )
            .order_by(Conversation.updated_at.desc(), Conversation.id)
        )
        return [chat_view(chat) for chat in chats]


class SearchQuery(BaseModel):
    query: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]


@router.post("/{kb_id}/search")
async def search_library(kb_id: UUID, body: SearchQuery, user: Identity, db: Database):
    from fastapi import HTTPException

    from model_stream import ModelFailure
    from search import retrieve

    try:
        return await retrieve(db, user.id, kb_id, body.query, strategy="bm25")
    except ModelFailure as exc:
        raise HTTPException(503, str(exc)) from None
