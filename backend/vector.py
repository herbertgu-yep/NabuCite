"""Versioned vector collections for published document attempts."""

import hashlib
import json
import math
import os
from contextvars import ContextVar
from urllib.parse import urlsplit

turn_account = ContextVar("retrieval_account", default=None)
audit_calls = ContextVar("vector_audit_calls", default=None)

import httpx

from config import model
from model_stream import ModelFailure
from telemetry import CALLS, timed

DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
COLLECTION = (
    "nb_"
    + hashlib.sha256(
        f"{os.getenv('VECTOR_NAMESPACE', 'main')}|{model.base_url}|{model.embedding_model}|{DIM}|cosine|v1".encode()
    ).hexdigest()[:24]
)


async def gateway(path, body):
    CALLS.labels("embedding" if path.endswith("/embeddings") else "rerank").inc()
    account = turn_account.get()
    if account:
        from database import DepartmentMember, Usage

        db, user_id, answer_id, version_id = account
        async with db.begin() as session:
            department = await session.get(DepartmentMember, user_id)
            usage = Usage(
                user_id=user_id,
                answer_id=answer_id,
                version_id=version_id,
                department_id=department.org_id,
                purpose="embedding" if path.endswith("/embeddings") else "rerank",
            )
            session.add(usage)
    try:
        async with httpx.AsyncClient(timeout=40) as c:
            r = await c.post(
                path,
                headers={"Authorization": "Bearer " + model.api_key.get_secret_value()},
                json=body,
            )
            r.raise_for_status()
            data = r.json()
            if audit_calls.get() is not None:
                audit_calls.get().append(
                    {"model": body["model"], "usage": data.get("usage")}
                )
            if account:
                stats = data.get("usage") or {}
                count = stats.get("prompt_tokens", stats.get("input_tokens"))
                total = stats.get("total_tokens")
                if (
                    isinstance(count, int)
                    and isinstance(total, int)
                    and total >= count >= 0
                ):
                    async with db.begin() as session:
                        saved = await session.get(Usage, usage.id)
                        saved.input_tokens = count
                        saved.output_tokens = total - count
                        saved.source = "reported"
            return data
    except (httpx.HTTPError, ValueError) as exc:
        raise ModelFailure(
            "检索模型接口失败："
            + (
                str(exc.response.status_code)
                if isinstance(exc, httpx.HTTPStatusError)
                else type(exc).__name__
            )
        ) from None


@timed("embedding")
async def embed(texts):
    data = await gateway(
        model.base_url.rstrip("/") + "/embeddings",
        {"model": model.embedding_model, "input": texts},
    )
    try:
        rows = sorted(data["data"], key=lambda r: r["index"])
        if not all(
            isinstance(r.get("embedding"), list)
            and all(isinstance(v, (float, int)) for v in r["embedding"])
            for r in rows
        ):
            raise ValueError
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ModelFailure("Embedding 响应格式异常。") from None
    if [r["index"] for r in rows] != list(range(len(texts))) or any(
        len(r["embedding"]) != DIM or not all(map(math.isfinite, r["embedding"]))
        for r in rows
    ):
        raise ModelFailure("Embedding 返回数量或维度不一致。")
    return [r["embedding"] for r in rows]


@timed("rerank")
async def rerank(query, rows):
    u = urlsplit(model.base_url)
    data = await gateway(
        f"{u.scheme}://{u.netloc}/api/v1/services/rerank/text-rerank/text-rerank",
        {
            "model": model.rerank_model,
            "input": {
                "query": query,
                "documents": [r["section"] + "\n" + r["text"] for r in rows],
            },
            "parameters": {"top_n": len(rows)},
        },
    )
    try:
        order = data["output"]["results"]
        if not all(
            isinstance(r["index"], int) and math.isfinite(r["relevance_score"])
            for r in order
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ModelFailure("重排响应格式异常。") from None
    if sorted(r["index"] for r in order) != list(range(len(rows))):
        raise ModelFailure("重排结果编号无效。")
    return [rows[r["index"]] | {"score": r["relevance_score"]} for r in order]


import asyncio
from functools import lru_cache


@lru_cache(maxsize=1)
def client():
    from pymilvus import MilvusClient

    return MilvusClient(
        uri=os.getenv("MILVUS_URL", "http://127.0.0.1:19530"), timeout=5
    )


def ensure_collection():
    from pymilvus import DataType

    service = client()
    if service.has_collection(COLLECTION):
        return
    schema = service.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=80)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=DIM)
    for field in ("kb", "attempt", "chunk"):
        schema.add_field(field, DataType.VARCHAR, max_length=36)
    indexes = service.prepare_index_params()
    indexes.add_index(
        field_name="vector",
        index_type="HNSW",
        metric_type="COSINE",
        params={"M": 16, "efConstruction": 160},
    )
    service.create_collection(
        collection_name=COLLECTION, schema=schema, index_params=indexes
    )


async def index_vectors(doc, chunks):
    await asyncio.to_thread(ensure_collection)
    for i in range(0, len(chunks), 8):
        batch = chunks[i : i + 8]
        vectors = await embed([c.section + "\n" + c.text for c in batch])
        await asyncio.to_thread(
            client().upsert,
            COLLECTION,
            data=[
                {
                    "id": str(doc.attempt) + ":" + str(c.id),
                    "kb": str(doc.knowledge_base_id),
                    "chunk": str(c.id),
                    "attempt": str(doc.attempt),
                    "vector": v,
                }
                for c, v in zip(batch, vectors, strict=True)
            ],
        )
    await asyncio.to_thread(client().flush, COLLECTION)
    count = await asyncio.to_thread(
        client().query,
        COLLECTION,
        filter=f'attempt == "{doc.attempt}"',
        output_fields=["count(*)"],
        consistency_level="Strong",
    )
    if not count or count[0]["count(*)"] != len(chunks):
        raise ModelFailure("向量索引记录数校验失败。")


@timed("vector")
async def vector_hits(query, kb, docs, query_vector=None):
    vector = query_vector if query_vector is not None else (await embed([query]))[0]
    attempts = json.dumps([str(d.attempt) for d in docs])
    try:
        result = await asyncio.to_thread(
            client().search,
            collection_name=COLLECTION,
            data=[vector],
            anns_field="vector",
            limit=24,
            filter=f'kb == "{kb}" and attempt in {attempts}',
            output_fields=["chunk"],
            search_params={"metric_type": "COSINE", "params": {"ef": 64}},
            consistency_level="Strong",
        )
    except Exception as exc:
        raise ModelFailure("Milvus 向量检索失败。") from exc
    return [{"_id": r["entity"]["chunk"], "_score": r["distance"]} for r in result[0]]


def fuse(*lists):
    scores = {}
    for hits in lists:
        for pos, chunk in enumerate(dict.fromkeys(h["_id"] for h in hits), 1):
            scores[chunk] = scores.get(chunk, 0) + 1 / (60 + pos)
    return [
        {"_id": k, "_score": scores[k]}
        for k in sorted(scores, key=lambda k: (-scores[k], k))[:24]
    ]
