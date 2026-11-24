import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from context import Step
from knowledge_turn import compatible_stream, respond
from model_stream import ModelFailure, ToolCall, model_text


class ToolProtocols(unittest.IsolatedAsyncioTestCase):
    async def test_split_native_call_and_usage(self):
        chunks = [
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "function": {"name": "search_", "arguments": '{"qu'},
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "function": {"name": "knowledge", "arguments": 'ery":"登记"}'},
                    }
                ]
            },
        ]

        async def lines():
            for i, delta in enumerate(chunks):
                yield "data: " + json.dumps(
                    {
                        "choices": [
                            {
                                "delta": delta,
                                "finish_reason": "tool_calls" if i else None,
                            }
                        ]
                    }
                )
                yield ""
            yield 'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":4}}'
            yield ""
            yield "data: [DONE]"
            yield ""

        usage = {}
        parts = [p async for p in model_text(lines(), 200, usage, True)]
        self.assertEqual(
            parts, [ToolCall("call_1", "search_knowledge", '{"query":"登记"}')]
        )
        self.assertEqual(usage["completion_tokens"], 4)

    async def test_structured_adapter(self):
        async def native(*args, response_format):
            self.assertTrue(response_format["json_schema"]["strict"])
            yield '{"action":"read_passage","value":"'
            yield r'{\"source\":2}"}'

        parts = [p async for p in compatible_stream(native)]
        self.assertEqual(parts[0].name, "read_passage")
        self.assertEqual(json.loads(parts[0].arguments), {"source": 2})

    async def test_tool_scope_budget_and_cache(self):
        source = {
            "chunk_id": "a",
            "document_id": "b",
            "page_start": 1,
            "page_end": 1,
            "section": "条款",
            "filename": "手册",
            "knowledge_base_id": "c",
            "text": "制度片段",
            "score": 1,
        }
        chat = SimpleNamespace(id=1, owner_id=1, knowledge_base_id="c")
        version = SimpleNamespace(window=8192, output=256)
        first = ToolCall("a", "search_knowledge", '{"query":"版本条件"}')
        for second in (ToolCall("b", "read_passage", '{"source":1}'), first):
            calls = []

            async def upstream(
                purpose, messages, *, tools, compatibility, second=second, calls=calls
            ):
                calls.append(tools)
                yield [first, second, "原文条款 [1]。"][len(calls) - 1]

            refs = []
            with (
                patch("knowledge_turn.retrieve", return_value=[source]) as search,
                patch("knowledge_turn.read_source", return_value=[source]) as read,
            ):
                parts = [
                    p
                    async for p in respond(
                        None,
                        chat,
                        SimpleNamespace(content="附注中还有什么条件？"),
                        [],
                        version,
                        upstream,
                        refs,
                        [],
                        {"elapsed": lambda: 0},
                    )
                ]
                search.assert_awaited_once()
                self.assertEqual(read.await_count, int(second.name == "read_passage"))
                self.assertEqual([r["number"] for r in refs], [1])
                self.assertEqual(len(calls), 3)
                self.assertIsNone(calls[-1])
                self.assertEqual(
                    "".join(p for p in parts if not isinstance(p, Step)),
                    "原文条款 [1]。",
                )

        async def unsafe(*args, **kwargs):
            yield ToolCall(
                "x", "search_knowledge", '{"query":"a","knowledge_base_id":"foreign"}'
            )

        with (
            patch("knowledge_turn.retrieve") as search,
            self.assertRaises(ModelFailure),
        ):
            async for _ in respond(
                None,
                chat,
                SimpleNamespace(content="登记"),
                [],
                version,
                unsafe,
                [],
                [],
                {"elapsed": lambda: 0},
            ):
                pass
        search.assert_not_called()

    async def test_reformat_previous_answer_reads_its_source(self):
        source = {
            "number": 4,
            "chunk_id": "old",
            "document_id": "doc",
            "section": "报销",
            "page_start": 2,
        }
        prior = SimpleNamespace(
            role="assistant",
            status="done",
            withdrawn=False,
            citations=[source],
            reply_to=1,
            request_id="old",
            content="上轮正文",
        )
        original = SimpleNamespace(
            id=1, role="user", request_id="old", content="先前问题"
        )
        chat = SimpleNamespace(owner_id="employee", knowledge_base_id="library")
        calls = []

        async def upstream(purpose, messages, **kwargs):
            calls.append(messages[-1]["role"])
            if len(calls) == 1:
                yield ToolCall("copy", "read_passage", '{"source":4}')
            else:
                yield "整理后的事实 [4]。"

        evidence = source | {"filename": "示例.pdf", "text": "制度原文", "score": None}
        version = SimpleNamespace(window=8192, output=256)
        with (
            patch("knowledge_turn.retrieve") as search,
            patch("knowledge_turn.read_source", return_value=[evidence]) as read,
        ):
            parts = [
                p
                async for p in respond(
                    None,
                    chat,
                    SimpleNamespace(content="换成清单给我看看"),
                    [original, prior],
                    version,
                    upstream,
                    [],
                    [],
                    {"elapsed": lambda: 0},
                )
            ]
            read.assert_awaited_once_with(None, chat, source)
            search.assert_not_called()
            self.assertIn(
                "整理后的事实 [4]", "".join(p for p in parts if isinstance(p, str))
            )
            self.assertEqual(calls, ["user", "tool"])


def test_evaluation_distinguishes_same_page_and_wrong_citation():
    from evaluate_search import evidence_hits
    from evaluate_turns import score

    anchor = {"document_id": "doc", "position": 2, "quote": "保存七天"}
    hit = {"document_id": "doc", "page_start": 2, "page_end": 2, "text": "仅限内部"}
    assert (
        evidence_hits(
            [hit, hit | {"document_id": "other", "text": "保存七天"}], [anchor]
        )
        == 0
    )
    assert evidence_hits([hit | {"text": "备份保存七天"}], [anchor]) == 1
    turn = {
        "question": "备份呢？",
        "terminal": "done",
        "conversation_id": "A",
        "steps": [{"stage": "retrieve", "status": "active"}],
        "query": "备份保存期",
        "answer": "备份保留一周[1]。",
        "citations": [{"number": 1, "chunk_id": "a"}],
        "readback": [
            {"chunk_id": "a", "excerpt": "仅限内部"},
            {"chunk_id": "b", "excerpt": "保存七天"},
        ],
    }
    labels = [
        {
            "turn": 0,
            "question": turn["question"],
            "tools": ["read"],
            "query_terms": ["备份", "归档"],
            "claims": [{"text": "备份保留一周", "quotes": ["保存七天"]}],
        }
    ]
    row = score([turn], labels)[0]
    assert not row["route_correct"] and not row["query_terms_present"]
    assert row["supported_claims"] == 0 and row["reviewed_claims"] == 1
