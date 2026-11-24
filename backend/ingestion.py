import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid5

from celery import Celery
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config import database_url
from database import Chunk, Document
from document_formats import DocumentError, markdown, pdf_chunks, word_document
from search import index_chunks
from storage import BUCKET, objects
from telemetry import timed

jobs = Celery(
    "nabucite",
    broker=os.environ.get("BROKER_URL")
    or f"amqp://nabucite:{os.environ['RABBIT_PASSWORD']}@127.0.0.1:{os.getenv('RABBIT_PORT', '55672')}//",
)
jobs.conf.update(
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    task_publish_retry=False,
    broker_connection_timeout=5,
)


def parse_pdf(path, document_id):
    from docling.datamodel.base_models import ConversionStatus, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import TableItem

    options = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
    result = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    ).convert(path)
    if result.status != ConversionStatus.SUCCESS:
        raise ValueError("PDF 解析未完整完成。")
    doc = result.document
    chunks = []
    section = ""
    section_ref = "root"
    page_titles = {}
    introductions = {}
    preceding = {}
    for item, _ in doc.iterate_items():
        pages = [p.page_no for p in getattr(item, "prov", [])]
        if not pages:
            continue
        label = item.label.value
        if label in ("title", "section_header"):
            preceding.pop(min(pages), None)
            section = item.text.strip()
            section_ref = item.self_ref
            page_titles.setdefault(min(pages), section)
            continue
        page = min(pages)
        heading = " / ".join(
            dict.fromkeys(x for x in (page_titles.get(page), section) if x)
        )
        if isinstance(item, TableItem):
            # 每行单独检索，同时保留城市、金额与版本列的对应关系。
            table = item.export_to_dataframe(doc=doc)
            columns = list(map(str, table.columns))
            parts = [
                "\n".join(
                    dict.fromkeys(
                        value
                        for value in (
                            introductions.get(page),
                            preceding.get(page),
                            " | ".join(
                                f"{k}: {v}" for k, v in zip(columns, row, strict=True)
                            ),
                        )
                        if value
                    )
                )
                for row in table.itertuples(index=False, name=None)
            ]
        elif label not in ("page_header", "page_footer", "picture"):
            text_content = getattr(item, "text", "")
            # 指代前段的条款需附上适用条件，只扩展同章同页的相邻原文。
            previous = preceding.get(page)
            parts = [
                previous + "\n" + text_content
                if previous
                and text_content.lstrip().startswith(
                    ("上述", "前款", "同上", "在此基础上")
                )
                else text_content
            ]
            if text_content.strip():
                # 同页引言与表前说明保留适用口径，表行中的版本信息不依赖模型猜测。
                introductions.setdefault(page, text_content.strip())
                preceding[page] = text_content.strip()
        else:
            continue
        for i, part in enumerate(parts):
            body = part.strip()
            for offset in range(0, len(body), 1600):
                key = f"{item.self_ref}/{i}"
                chunks.append(
                    Chunk(
                        id=uuid5(document_id, f"{key}/{offset}" if offset else key),
                        document_id=document_id,
                        section=heading,
                        parent_id=uuid5(document_id, section_ref),
                        page_start=min(pages),
                        page_end=max(pages),
                        text=body[offset : offset + 1600],
                    )
                )
    return chunks


def parse_document(path, doc_id):
    if path.suffix == ".pdf":
        chunks = pdf_chunks(path, doc_id, parse_pdf)
    elif path.suffix in (".doc", ".docx"):
        chunks = word_document(path, doc_id)
    else:
        chunks = markdown(path, doc_id)
    if not chunks:
        raise DocumentError("未提取到可检索文字，请检查文档内容或扫描清晰度。")
    # 同一结构块内按解析顺序补读，页码和 UUID 均不能表示段落顺序。
    previous = None
    for ordinal, chunk in enumerate(chunks):
        key = chunk.parent_id or chunk.section
        if key != previous:
            parent = uuid5(doc_id, f"parent/{chunk.id}")
            previous = key
        chunk.parent_id = parent
        chunk.ordinal = ordinal
    return chunks


@timed("ingestion")
async def process(doc_id, attempt):
    engine = create_async_engine(database_url(), hide_parameters=True)
    try:
        async with engine.connect() as lock:
            # 连接级锁在 worker 异常退出时自动释放，重投同一任务可以恢复。
            await lock.execute(
                text("select pg_advisory_lock(:id)"),
                {"id": int.from_bytes(doc_id.bytes[:8], "big", signed=True)},
            )
            await lock.commit()
            db = async_sessionmaker(engine, expire_on_commit=False)
            claim = update(Document).where(
                Document.id == doc_id,
                Document.attempt == attempt,
                Document.status.in_(["pending", "processing"]),
            )
            async with db.begin() as session:
                doc = await session.scalar(
                    claim.values(status="processing").returning(Document)
                )
            if doc is None:
                return
            try:
                with TemporaryDirectory() as tmp:
                    path = Path(tmp) / ("source" + Path(doc.filename).suffix.lower())
                    objects().download_file(BUCKET, doc.object_key, str(path))
                    chunks = parse_document(path, doc.id)
                await index_chunks(doc, chunks)
                async with db.begin() as session:
                    current = await session.scalar(
                        select(Document).where(Document.id == doc.id).with_for_update()
                    )
                    if current.attempt != attempt:
                        return
                    await session.execute(
                        delete(Chunk).where(Chunk.document_id == doc.id)
                    )
                    session.add_all(chunks)
                    current.vector_versions = []
                    current.status, current.chunk_count = "ready", len(chunks)
            except Exception as exc:  # noqa: BLE001 -- 第三方解析器异常也必须结束本次入库。
                from telemetry import FAILURES

                FAILURES.labels("document").inc()
                reason = (
                    str(exc)
                    if isinstance(exc, DocumentError)
                    or type(exc) is ValueError
                    and str(exc)
                    in (
                        "PDF 解析未完整完成。",
                        "片段索引失败。",
                    )
                    else f"入库失败（{type(exc).__name__}），请检查原文件、解析模型与存储或索引服务。"
                )
                async with db.begin() as session:
                    await session.execute(claim.values(status="failed", failure=reason))
    finally:
        await engine.dispose()


@jobs.task(name="documents.ingest", acks_late=True, reject_on_worker_lost=True)
def ingest(document_id, attempt):
    asyncio.run(process(UUID(document_id), UUID(attempt)))
    if os.getenv("RETRIEVAL_STRATEGY", "bm25") != "bm25":
        vectorize.delay(document_id)


async def backfill(doc_id):
    from database import KnowledgeBase
    from vector import COLLECTION, index_vectors

    engine = create_async_engine(database_url(), hide_parameters=True)
    try:
        db = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.connect() as lock:
            await lock.execute(
                text("select pg_advisory_lock(:id)"),
                {"id": int.from_bytes(doc_id.bytes[:8], "big", signed=True)},
            )
            await lock.commit()
            async with db() as session:
                doc = await session.scalar(
                    select(Document)
                    .join(KnowledgeBase)
                    .where(
                        Document.id == doc_id,
                        Document.status == "ready",
                        KnowledgeBase.deleted.is_(False),
                    )
                )
                if not doc or COLLECTION in doc.vector_versions:
                    return
                chunks = list(
                    await session.scalars(
                        select(Chunk)
                        .where(Chunk.document_id == doc.id)
                        .order_by(Chunk.id)
                    )
                )
            await index_vectors(doc, chunks)
            async with db.begin() as session:
                current = await session.get(Document, doc_id, with_for_update=True)
                if (
                    current
                    and current.attempt == doc.attempt
                    and current.status == "ready"
                ):
                    current.vector_versions = sorted(
                        set(current.vector_versions) | {COLLECTION}
                    )
    finally:
        await engine.dispose()


@jobs.task(name="documents.vectorize", acks_late=True)
def vectorize(document_id):
    asyncio.run(backfill(UUID(document_id)))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("document_id", type=UUID)
    asyncio.run(backfill(parser.parse_args().document_id))


from celery.signals import worker_ready


@worker_ready.connect
def serve_worker_metrics(**kwargs):
    from prometheus_client import start_http_server

    start_http_server(9101)
