import json
import unittest

from fastapi.testclient import TestClient
from pydantic import SecretStr

from main import app
from model_stream import (
    ModelFailure,
    data_records,
    model_text,
    stream_reply,
    upstream_failure,
)


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_browser_preflight(self):
        for origin in ["http://localhost:5173", "http://127.0.0.1:5173"]:
            response = self.client.options(
                "/api/conversations/00000000-0000-0000-0000-000000000001/messages",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization,content-type",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["access-control-allow-origin"], origin)

    def test_upstream_failure_categories(self):
        self.assertIn("模型凭据", str(upstream_failure(401)))
        self.assertIn("模型或端点", str(upstream_failure(400, "model_not_found")))
        self.assertIn("配额", str(upstream_failure(429)))


class RecordTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiline_data_and_unterminated_record(self):
        async def lines():
            for line in [
                ": heartbeat",
                "",
                "data: one",
                "data: two",
                "",
                "data: unfinished",
            ]:
                yield line

        self.assertEqual([data async for data in data_records(lines())], ["one\ntwo"])

    async def test_model_protocol_ignores_metadata_and_keeps_text_boundaries(self):
        async def lines():
            for chunk in [
                {
                    "choices": [
                        {
                            "delta": {
                                "role": "assistant",
                                "reasoning_content": "internal",
                            }
                        }
                    ]
                },
                {"choices": [{"delta": {"content": "一"}}]},
                {"choices": [{"delta": {"content": "\n二"}}]},
                {"choices": [], "usage": {}},
            ]:
                yield "data: " + json.dumps(chunk)
                yield ""
            yield "data: [DONE]"
            yield ""

        self.assertEqual(
            [part async for part in model_text(lines(), 200)], ["一", "\n二"]
        )

    async def test_upstream_eof_after_text_is_interrupted(self):
        async def lines():
            yield 'data: {"choices":[{"delta":{"content":"一"}}]}'
            yield ""

        stream = model_text(lines(), 200)
        self.assertEqual(await anext(stream), "一")
        with self.assertRaisesRegex(ModelFailure, "提前关闭"):
            await anext(stream)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_endpoint_produces_model_failure(self):
        with self.assertRaisesRegex(ModelFailure, "MODEL_BASE_URL"):
            async for _ in stream_reply(
                [{"role": "user", "content": "配置检查"}],
                "invalid://",
                "qwen3.7-plus",
                SecretStr(""),
            ):
                self.fail("无效端点不应产生正文")
