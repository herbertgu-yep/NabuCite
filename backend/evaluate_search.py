"""Measure retrieval without generating answers or calling a judge."""

import argparse
import asyncio
import json
import math
import time
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from database import Document, User, sessions
from evaluate_turns import contains
from search import retrieve
from vector import COLLECTION


def evidence_hits(hits, anchors):
    return sum(
        any(
            h["document_id"] == anchor["document_id"]
            and h["page_start"] <= anchor["position"] <= h["page_end"]
            and contains(h["text"], anchor["quote"])
            for h in hits[:10]
        )
        for anchor in anchors
    )


async def run(dataset, strategy, username):
    data = json.loads(dataset.read_text())
    kb = UUID(data["knowledge_base_id"])
    db = sessions()
    async with db() as s:
        uid = await s.scalar(
            select(User.id).where(User.enabled, User.username == username)
        )
        docs = (
            await s.scalars(
                select(Document).where(
                    Document.knowledge_base_id == kb, Document.status == "ready"
                )
            )
        ).all()
    if not uid or {str(d.id) for d in docs} != {data["document_id"]}:
        raise ValueError("知识库快照或评测账号不匹配")
    rows = []
    for case in data["cases"]:
        start = time.perf_counter()
        hits = await retrieve(db, uid, kb, case["query"], strategy)
        ms = 1000 * (time.perf_counter() - start)
        top = [
            {"id": h["chunk_id"], "page": h["page_start"], "section": h["section"]}
            for h in hits[:10]
        ]
        pages = set(case.get("pages", []))
        found = pages & {h["page"] for h in top}
        rows.append(
            {
                "question": case["question"],
                "ms": round(ms, 2),
                "hits": top,
                "required": len(pages),
                "found": len(found),
                "all": bool(pages and found == pages),
            }
        )
        if "evidence" in case:
            anchors = case["evidence"]
            rows[-1]["anchors_found"] = evidence_hits(hits, anchors)
            rows[-1]["anchors_required"] = len(anchors)
    required = sum(r["required"] for r in rows)
    anchors = sum(r.get("anchors_required", 0) for r in rows)
    return {
        "strategy": strategy,
        "attempt": str(docs[0].attempt),
        "vector_collection": COLLECTION if strategy != "bm25" else None,
        "rows": rows,
        "recall": sum(r["found"] for r in rows) / required if required else None,
        "evidence_recall_at_10": sum(r.get("anchors_found", 0) for r in rows) / anchors
        if anchors
        else None,
        "p95_ms": sorted(r["ms"] for r in rows)[math.ceil(0.95 * len(rows)) - 1],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("strategy", choices=["bm25", "vector", "hybrid", "rerank"])
    parser.add_argument("username")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(run(args.dataset, args.strategy, args.username)),
            ensure_ascii=False,
            indent=2,
        )
    )
