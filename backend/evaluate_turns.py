"""Score annotated observations from captured turns without another model call."""

import argparse
import json
import re
import unicodedata
from pathlib import Path


def normalize(text):
    return "".join(unicodedata.normalize("NFKC", text).split()).casefold()


def contains(text, quote):
    return bool(normalize(quote)) and normalize(quote) in normalize(text)


def score(turns, labels):
    rows = []
    for label in labels:
        index = label["turn"]
        if not 0 <= index < len(turns):
            raise ValueError("评测轮次越界")
        turn = turns[index]
        if turn["question"] != label["question"] or turn["terminal"] != "done":
            raise ValueError("所选轮次与标注不匹配或尚未完成")
        if "previous_question" in label:
            previous = next(
                (
                    t
                    for t in reversed(turns[:index])
                    if t["conversation_id"] == turn["conversation_id"]
                ),
                None,
            )
            if not previous or previous["question"] != label["previous_question"]:
                raise ValueError("缺少标注的上一轮问题")
        stages = [
            s["stage"]
            for s in turn["steps"]
            if s["status"] == "active" and s["stage"] in ("retrieve", "read")
        ]
        refs = {r["number"]: r["chunk_id"] for r in turn["citations"]}
        evidence = {r["chunk_id"]: r["excerpt"] for r in turn["readback"]}
        claims = []
        for claim in label.get("claims", []):
            # 人工标注结论及支持它的原文，核对结论实际所引的片段。
            if (
                not claim["text"].strip()
                or not claim["quotes"]
                or not all(claim["quotes"])
            ):
                raise ValueError("结论及金标准证据不能为空")
            match = re.search(
                re.escape(claim["text"]) + r"\s*((?:\[\d+\]\s*)+)", turn["answer"]
            )
            numbers = re.findall(r"\d+", match[1]) if match else []
            cited = [evidence.get(refs.get(int(n)), "") for n in numbers]
            claims.append(
                all(any(contains(text, q) for text in cited) for q in claim["quotes"])
            )
        rows.append(
            {
                "turn": index,
                "route_correct": stages == label["tools"],
                "query_terms_present": all(
                    contains(turn.get("query") or "", term)
                    for term in label["query_terms"]
                )
                if label.get("query_terms")
                else None,
                "supported_claims": sum(claims),
                "reviewed_claims": len(claims),
            }
        )
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("turns", type=Path)
    parser.add_argument("labels", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            score(
                json.loads(args.turns.read_text()), json.loads(args.labels.read_text())
            ),
            indent=2,
        )
    )
