from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from tempfile import SpooledTemporaryFile
from urllib.parse import quote, unquote
from uuid import UUID, uuid4

from boto3.exceptions import Boto3Error
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from kombu.exceptions import KombuError
from sqlalchemy import and_, or_, select, update
from starlette.concurrency import run_in_threadpool

from access import membership
from auth import Identity
from database import Chunk, Database, Document
from document_formats import MIME, DocumentError, validate
from storage import BUCKET, objects, put_document

router = APIRouter(prefix="/api/knowledge-bases/{kb_id}/documents")


def view(doc):
    return {
        k: getattr(doc, k)
        for k in ("id", "filename", "status", "failure", "chunk_count", "updated_at")
    }


async def enqueue(db, doc):
    def publish():
        from ingestion import ingest

        ingest.apply_async(args=[str(doc.id), str(doc.attempt)], retry=False)

    try:
        await run_in_threadpool(publish)
    except (KombuError, OSError, KeyError):
        async with db.begin() as session:
            await session.execute(
                update(Document)
                .where(
                    Document.id == doc.id,
                    Document.attempt == doc.attempt,
                    Document.status == "pending",
                )
                .values(status="failed", failure="入库队列不可用，请重试。")
            )


@router.get("")
async def listing(kb_id: UUID, user: Identity, db: Database, manage: bool = False):
    async with db() as session:
        await membership(session, user.id, kb_id, admin=manage)
        docs = await session.scalars(
            select(Document)
            .where(Document.knowledge_base_id == kb_id)
            .order_by(Document.updated_at.desc())
        )
        return [view(d) for d in docs]


@router.post("", status_code=202)
async def upload(kb_id: UUID, request: Request, user: Identity, db: Database):
    async with db() as session:
        await membership(session, user.id, kb_id, admin=True)
    filename = PurePath(unquote(request.headers.get("x-filename", ""))).name
    suffix = PurePath(filename).suffix.lower()
    if suffix not in MIME or len(filename) > 255:
        raise HTTPException(422, "请选择 PDF、Word 或 Markdown 文件。")
    doc_id = uuid4()
    doc = Document(
        id=doc_id,
        knowledge_base_id=kb_id,
        filename=filename,
        object_key=f"{kb_id}/{doc_id}{suffix}",
    )
    with SpooledTemporaryFile(max_size=1024 * 1024) as file:
        async for part in request.stream():
            if file.tell() + len(part) > 32 * 1024 * 1024:
                raise HTTPException(413, "文件大小上限为 32 MiB。")
            file.write(part)
        file.seek(0)
        try:
            await run_in_threadpool(validate, file, suffix)
        except DocumentError as exc:
            raise HTTPException(422, str(exc)) from None
        file.seek(0)
        try:
            await run_in_threadpool(put_document, doc.object_key, file, MIME[suffix])
        except (BotoCoreError, Boto3Error, ClientError, OSError, KeyError):
            raise HTTPException(503, "原文件存储不可用。") from None
    async with db.begin() as session:
        await membership(session, user.id, kb_id, admin=True)
        session.add(doc)
    await enqueue(db, doc)
    return {"id": doc.id}


async def document(session, kb_id, doc_id):
    doc = await session.get(Document, doc_id)
    if doc is None or doc.knowledge_base_id != kb_id:
        raise HTTPException(404, "文档不可访问。")
    return doc


@router.post("/{doc_id}/retry", status_code=202)
async def retry(kb_id: UUID, doc_id: UUID, user: Identity, db: Database):
    async with db.begin() as session:
        await membership(session, user.id, kb_id, admin=True)
        doc = await session.scalar(
            update(Document)
            .where(
                Document.id == doc_id,
                Document.knowledge_base_id == kb_id,
                or_(
                    Document.status == "failed",
                    and_(
                        Document.status == "pending",
                        Document.updated_at < datetime.now(UTC) - timedelta(minutes=5),
                    ),
                ),
            )
            .values(status="pending", failure=None, attempt=uuid4())
            .returning(Document)
        )
        if doc is None:
            await document(session, kb_id, doc_id)
            raise HTTPException(409, "仅可重试失败或排队超过五分钟的文档。")
    await enqueue(db, doc)
    return {"id": doc.id}


@router.get("/{doc_id}/chunks/{chunk_id}")
async def passage(
    kb_id: UUID, doc_id: UUID, chunk_id: UUID, user: Identity, db: Database
):
    async with db() as session:
        await membership(session, user.id, kb_id)
        doc = await document(session, kb_id, doc_id)
        chunk = await session.get(Chunk, chunk_id)
        if doc.status != "ready" or not chunk or chunk.document_id != doc_id:
            raise HTTPException(404, "片段不可访问。")
        return {
            "filename": doc.filename,
            "section": chunk.section,
            "text": chunk.text,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
        }


@router.get("/{doc_id}/original")
async def original(kb_id: UUID, doc_id: UUID, user: Identity, db: Database):
    async with db() as session:
        await membership(session, user.id, kb_id)
        doc = await document(session, kb_id, doc_id)

    def open_file():
        return objects().get_object(Bucket=BUCKET, Key=doc.object_key)["Body"]

    try:
        body = await run_in_threadpool(open_file)
    except (BotoCoreError, Boto3Error, ClientError, OSError, KeyError):
        raise HTTPException(503, "无法读取原文件。") from None

    def chunks():
        try:
            yield from body.iter_chunks(chunk_size=65536)
        finally:
            body.close()

    return StreamingResponse(
        chunks(),
        media_type=MIME[PurePath(doc.filename).suffix.lower()],
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": (
                "inline" if doc.filename.lower().endswith(".pdf") else "attachment"
            )
            + "; filename*=UTF-8''"
            + quote(doc.filename, safe=""),
        },
    )
