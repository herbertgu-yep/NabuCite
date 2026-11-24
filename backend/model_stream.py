import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
from pydantic import SecretStr


async def data_records(lines: AsyncIterator[str]) -> AsyncIterator[str]:
    """HTTPX 已解码 UTF-8 及行尾，此处只在完整的 SSE 记录边界分发。"""
    fields: list[str] = []
    async for line in lines:
        if not line:
            if fields:
                yield "\n".join(fields)
                fields.clear()
        elif line == "data" or line.startswith("data:"):
            fields.append(line[5:].removeprefix(" "))


class ModelFailure(Exception):
    """只携带可直接展示给用户的错误描述。"""


class ToolsUnsupported(ModelFailure):
    pass


def upstream_failure(status: int, code: object = None) -> ModelFailure:
    # 上游错误正文可能包含请求细节。只映射已知类别和 HTTP 状态。
    kind = str(code).lower()
    if status in (401, 403) or kind in {
        "invalid_api_key",
        "invalidapikey",
        "accessdenied",
    }:
        reason = "模型凭据无效或无权调用，请检查后端模型凭据和授权。"
    elif status == 404 or kind in {"model_not_found", "modelnotfound", "invalidmodel"}:
        reason = "模型或端点不可用，请检查 LLM_MODEL 和 MODEL_BASE_URL。"
    elif status == 429:
        reason = "模型调用受限，请检查配额或稍后重试。"
    elif status == 400:
        reason = "模型拒绝请求，请检查模型名、消息历史和上下文长度。"
    else:
        reason = "模型服务返回错误，请检查模型服务状态。"
    return ModelFailure(f"{reason}（上游 HTTP {status}）")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str

    def wire(self):
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


async def model_text(
    lines: AsyncIterator[str], status: int, usage=None, allow_tools=False
) -> AsyncIterator[str]:
    """只接受来自完整记录的正文增量；上游完成标记是正常返回的条件。"""
    received_text = False
    tool = ToolCall("", "", "")
    finish = None
    async for record in data_records(lines):
        if record == "[DONE]":
            if tool.id or tool.name or tool.arguments:
                if (
                    not allow_tools
                    or finish != "tool_calls"
                    or not tool.id
                    or not tool.name
                ):
                    raise ModelFailure("工具调用未完整结束。")
                yield tool
                return
            if not received_text:
                raise ModelFailure("模型已结束，未返回回答文本。")
            return
        chunk = json.loads(record)
        if chunk.get("error"):
            raise upstream_failure(status, chunk["error"].get("code"))
        report = chunk.get("usage")
        if (
            usage is not None
            and isinstance(report, dict)
            and all(
                type(report.get(k)) is int and report[k] >= 0
                for k in ("prompt_tokens", "completion_tokens")
            )
        ):
            usage.update({k: report[k] for k in ("prompt_tokens", "completion_tokens")})
        choices = chunk["choices"]
        if not choices:  # 兼容只含用量统计的结束块。
            continue
        choice = choices[0]
        finish = choice.get("finish_reason") or finish
        for fragment in choice["delta"].get("tool_calls", []):
            if not allow_tools or fragment.get("index") != 0:
                raise ModelFailure("每次决策只允许一个只读工具。")
            function = fragment.get("function", {})
            tool.id += fragment.get("id") or ""
            tool.name += function.get("name") or ""
            tool.arguments += function.get("arguments") or ""
            if len(tool.arguments) > 8192:
                raise ModelFailure("工具参数超出限制。")
        text = choice["delta"].get("content")
        if text is not None and not isinstance(text, str):
            raise ValueError("invalid content")
        if text:
            received_text = True
            yield text
        if choice.get("finish_reason") == "length":
            raise ModelFailure("回答达到模型长度限制，可继续追问。")
        if choice.get("finish_reason") == "content_filter":
            raise ModelFailure("模型服务停止了本次回答。")
    raise ModelFailure("模型连接提前关闭，未收到完成信号。")


async def stream_reply(
    messages: list[dict[str, str]],
    base_url: str,
    model: str,
    key: SecretStr,
    output_limit: int | None = None,
    usage: dict | None = None,
    *,
    tools=None,
    response_format=None,
) -> AsyncIterator[str]:
    """仅转发回答正文。正常完成、上游报错和意外断流有独立的结束语义。"""

    if not base_url.startswith(("https://", "http://")):
        raise ModelFailure("请检查 MODEL_BASE_URL，应为 HTTP(S) 地址。")
    try:
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10)) as client,
            client.stream(
                "POST",
                f"{base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {key.get_secret_value()}",
                    "Accept": "text/event-stream",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    **({"response_format": response_format} if response_format else {}),
                    **({"tools": tools, "tool_choice": "auto"} if tools else {}),
                    "stream_options": {"include_usage": True},
                    **({"max_tokens": output_limit} if output_limit else {}),
                },
            ) as response,
        ):
            if not response.is_success:
                try:
                    error = json.loads(await response.aread()).get("error", {})
                    if (
                        tools
                        and response.status_code in (400, 422)
                        and isinstance(error, dict)
                    ):
                        detail = str(error.get("message", "")).lower()
                        if "tool" in detail and any(
                            term in detail
                            for term in (
                                "not support",
                                "unsupported",
                                "not implemented",
                                "不支持",
                            )
                        ):
                            raise ToolsUnsupported("网关不支持原生工具调用。")
                    code = error.get("code") if isinstance(error, dict) else None
                except (ValueError, AttributeError):
                    code = None
                raise upstream_failure(response.status_code, code)
            if (
                response.headers.get("content-type", "").split(";")[0].strip()
                != "text/event-stream"
            ):
                raise ModelFailure("模型端点未返回 SSE，请检查 MODEL_BASE_URL。")
            async for text in model_text(
                response.aiter_lines(), response.status_code, usage, bool(tools)
            ):
                yield text
    except ImportError as exc:
        raise ModelFailure("后端网络代理依赖不可用，请检查模型网络配置。") from exc
    except httpx.TimeoutException as exc:
        raise ModelFailure("模型连接超时，请稍后重试。") from exc
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise ModelFailure(
            "无法连接模型或传输中断，请检查后端网络及 MODEL_BASE_URL。"
        ) from exc
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ModelFailure("模型流格式异常，无法继续读取回答。") from exc
