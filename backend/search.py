import asyncio
import json
import os

import httpx
from sqlalchemy import select

from access import membership
from database import Chunk, Document
from model_stream import ModelFailure
from telemetry import timed

INDEX = "nabucite-passages-v1"


def search_url():
    return (
        os.getenv(
            "ELASTICSEARCH_URL", f"http://127.0.0.1:{os.getenv('SEARCH_PORT', '59200')}"
        ).rstrip("/")
        + "/"
        + INDEX
    )


async def index_chunks(document, chunks):
    mapping = {
        key: {"type": "keyword"}
        for key in ("document_id", "knowledge_base_id", "attempt")
    }
    mapping.update(
        {key: {"type": "text", "analyzer": "cjk"} for key in ("section", "text")}
    )
    async with httpx.AsyncClient(
        trust_env=False, auth=("elastic", os.environ["SEARCH_PASSWORD"]), timeout=40
    ) as client:
        url = search_url()
        res = await client.put(url, json={"mappings": {"properties": mapping}})
        if (
            not res.is_success
            and res.json().get("error", {}).get("type")
            != "resource_already_exists_exception"
        ):
            res.raise_for_status()
        for i in range(0, len(chunks), 100):
            lines = []
            for chunk in chunks[i : i + 100]:
                lines.extend(
                    [
                        {"index": {"_id": str(chunk.id)}},
                        {
                            "document_id": str(document.id),
                            "knowledge_base_id": str(document.knowledge_base_id),
                            "attempt": str(document.attempt),
                            "text": chunk.text,
                            "section": chunk.section,
                        },
                    ]
                )
            res = await client.post(
                url + "/_bulk",
                content="".join(
                    json.dumps(line, ensure_ascii=False) + "\n" for line in lines
                ).encode(),
                headers={"Content-Type": "application/x-ndjson"},
            )
            res.raise_for_status()
            if res.json()["errors"]:
                raise ValueError("片段索引失败。")
        (await client.post(url + "/_refresh")).raise_for_status()


@timed("bm25")
async def bm25(query, kb_id, docs):
    # 每轮只放行已经完整发布的入库批次，失败任务留下的索引始终不可见。
    filters = [
        {"term": {"knowledge_base_id": str(kb_id)}},
        {"terms": {"document_id": [str(d.id) for d in docs]}},
        {"terms": {"attempt": [str(d.attempt) for d in docs]}},
    ]
    body = {
        "size": 24,
        "_source": False,
        "query": {
            "bool": {
                "filter": filters,
                "must": [
                    {"multi_match": {"query": query, "fields": ["text", "section^1.3"]}}
                ],
            }
        },
    }
    try:
        async with httpx.AsyncClient(
            trust_env=False, auth=("elastic", os.environ["SEARCH_PASSWORD"]), timeout=12
        ) as client:
            res = await client.post(search_url() + "/_search", json=body)
            res.raise_for_status()
            hits = res.json()["hits"]["hits"]
    except (httpx.HTTPError, KeyError, ValueError, ImportError) as exc:
        raise ModelFailure("知识库检索服务不可用，请稍后重试。") from exc
    return hits


@timed("retrieval")
async def retrieve(db, user_id, kb_id, query, strategy=None, query_vector=None):
    async with db() as session:
        await membership(session, user_id, kb_id)
        docs = (
            await session.scalars(
                select(Document).where(
                    Document.knowledge_base_id == kb_id, Document.status == "ready"
                )
            )
        ).all()
    if not docs:
        raise ModelFailure("当前知识库没有可用文档。")
    from vector import COLLECTION, fuse, rerank, vector_hits

    strategy = strategy or os.getenv("RETRIEVAL_STRATEGY", "bm25")
    if strategy not in {"bm25", "vector", "hybrid", "rerank"}:
        raise ModelFailure("检索策略配置无效。")
    if strategy != "bm25" and any(
        COLLECTION not in (d.vector_versions or []) for d in docs
    ):
        raise ModelFailure("当前版本向量索引尚未就绪。")
    if strategy == "bm25":
        hits = await bm25(query, kb_id, docs)
    elif strategy == "vector":
        hits = await vector_hits(query, kb_id, docs, query_vector)
    else:
        hits = fuse(
            *await asyncio.gather(
                bm25(query, kb_id, docs), vector_hits(query, kb_id, docs, query_vector)
            )
        )
    ranked = await passages(db, user_id, kb_id, docs, hits)
    return await rerank(query, ranked) if strategy == "rerank" and ranked else ranked


async def passages(db, user_id, kb_id, docs, hits):
    from uuid import UUID, uuid5

    ids = [UUID(hit["_id"]) for hit in hits]
    async with db() as session:
        await membership(session, user_id, kb_id)
        rows = await session.execute(
            select(Chunk, Document)
            .join(Document)
            .where(
                Chunk.id.in_(ids),
                Document.attempt.in_([d.attempt for d in docs]),
                Document.knowledge_base_id == kb_id,
                Document.status == "ready",
            )
        )
        by_id = {
            str(c.id): {
                "chunk_id": str(c.id),
                "document_id": str(d.id),
                "parent_id": str(
                    c.parent_id or uuid5(d.id, f"{c.page_start}:{c.section}")
                ),
                "knowledge_base_id": str(kb_id),
                "filename": d.filename,
                "section": c.section,
                "page_start": c.page_start,
                "page_end": c.page_end,
                "text": c.text,
            }
            for c, d in rows
        }
    ranked = [
        by_id[h["_id"]] | {"score": h["_score"]} for h in hits if h["_id"] in by_id
    ]
    return ranked
