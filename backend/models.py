import os
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import quote, urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, HttpUrl, SecretStr, model_validator
from sqlalchemy import select

from accounts import Admin
from auth import Identity
from config import model
from database import Database, DepartmentMember, ModelVersion, Publication, Usage
from model_stream import ModelFailure, stream_reply

router = APIRouter(prefix="/api")
ZONE = ZoneInfo("Asia/Shanghai")


async def initialize_model(session):
    if not await session.get(Publication, 1):
        version = ModelVersion(
            model=model.llm_model,
            base_url=model.base_url,
            window=int(os.getenv("MODEL_CONTEXT_WINDOW", "32768")),
            output=int(os.getenv("MODEL_OUTPUT_LIMIT", "2048")),
            credential="MODEL_API_KEY",
            published=True,
        )
        session.add(version)
        await session.flush()
        session.add(Publication(id=1, version_id=version.id))


def key_for(version):
    # 凭据绑定到后端登记的服务来源，修改草稿不能把已有 Key 发往另一台主机。
    registered = (
        model.base_url
        if version.credential == "MODEL_API_KEY"
        else os.getenv(
            version.credential.replace("NABUCITE_MODEL_KEY_", "NABUCITE_MODEL_URL_", 1),
            "",
        )
    )

    def origin(value):
        uri = urlsplit(str(value))
        return (
            uri.scheme,
            uri.hostname,
            uri.port or (443 if uri.scheme == "https" else 80),
        )

    if not registered or origin(version.base_url) != origin(registered):
        raise HTTPException(422, "凭据与服务来源不匹配，请检查后端登记的凭据和地址。")
    key = (
        model.api_key
        if version.credential == "MODEL_API_KEY"
        else SecretStr(os.environ.get(version.credential, ""))
    )
    if not key.get_secret_value():
        raise HTTPException(503, "模型凭据未配置，请检查后端配置。")
    return key


class ModelTarget(BaseModel):
    base_url: HttpUrl
    model: str = Field(min_length=1, max_length=160)
    credential: str = Field(pattern=r"^(MODEL_API_KEY|NABUCITE_MODEL_KEY_[A-Z0-9_]+)$")

    @model_validator(mode="after")
    def valid(self):
        if (
            self.base_url.scheme != "https"
            or self.base_url.username
            or self.base_url.password
            or self.base_url.query
            or self.base_url.fragment
        ):
            raise ValueError("模型地址须使用不含凭据的 HTTPS 地址。")
        return self


class Draft(ModelTarget):
    window: int = Field(ge=1024, le=4000000)
    output: int = Field(ge=32)

    @model_validator(mode="after")
    def valid_budget(self):
        if self.window - self.output < 512:
            raise ValueError("上下文窗口不足。")
        return self


@router.post("/admin/models/limits")
async def model_limits(body: ModelTarget, admin: Admin):
    base = str(body.base_url).rstrip("/")
    key = key_for(body)
    record = {}
    note = ""
    try:
        async with httpx.AsyncClient(
            timeout=5, headers={"Authorization": "Bearer " + key.get_secret_value()}
        ) as client:
            response = await client.get(base + "/models/" + quote(body.model, safe=""))
            if response.status_code in (404, 405):
                response = await client.get(base + "/models")
            response.raise_for_status()
            payload = response.json()
            entries = (
                payload.get("data", [payload]) if isinstance(payload, dict) else []
            )
            if isinstance(entries, dict):
                entries = [entries]
            record = (
                next(
                    (
                        entry
                        for entry in entries
                        if isinstance(entry, dict) and entry.get("id") == body.model
                    ),
                    {},
                )
                if isinstance(entries, list)
                else {}
            )
    except httpx.HTTPStatusError as exc:
        note = f"网关模型信息接口返回 HTTP {exc.response.status_code}。"
    except (httpx.HTTPError, ValueError):
        note = "无法读取网关模型信息。"

    limits = {}
    for field, names in (
        (
            "window",
            ("context_length", "context_window", "max_model_len", "max_context_length"),
        ),
        ("output", ("max_output_tokens", "max_completion_tokens")),
    ):
        for section in (record, record.get("top_provider"), record.get("metadata")):
            if not isinstance(section, dict):
                continue
            value = next(
                (
                    section[n]
                    for n in names
                    if type(section.get(n)) is int and section[n] > 0
                ),
                None,
            )
            if value:
                limits[field] = {"value": value, "source": "网关模型接口"}
                break
    if body.model == "qwen3.7-plus":
        for field, value in (("window", 1000000), ("output", 131072)):
            limits.setdefault(
                field,
                {
                    "value": value,
                    "source": "阿里云公开规格（当前网关未报告）",
                    "url": "https://help.aliyun.com/zh/model-studio/qwen3-7-plus",
                },
            )
    return {"limits": limits, "note": note}


@router.get("/admin/models")
async def versions(admin: Admin, db: Database):
    async with db() as session:
        current = await session.get(Publication, 1)
        rows = await session.scalars(
            select(ModelVersion).order_by(ModelVersion.created_at.desc())
        )
        return [
            {
                "id": v.id,
                "model": v.model,
                "base_url": v.base_url,
                "window": v.window,
                "output": v.output,
                "credential": v.credential,
                "published": v.published,
                "active": v.id == current.version_id,
            }
            for v in rows
        ]


@router.post("/admin/models", status_code=201)
async def draft(body: Draft, admin: Admin, db: Database):
    async with db.begin() as session:
        row = ModelVersion(
            **(body.model_dump() | {"base_url": str(body.base_url).rstrip("/")})
        )
        session.add(row)
        await session.flush()
        return {"id": row.id}


@router.post("/admin/models/{version_id}/publish")
async def publish(version_id: UUID, admin: Admin, db: Database):
    async with db.begin() as session:
        current = await session.get(Publication, 1, with_for_update=True)
        row = await session.get(ModelVersion, version_id)
        if not row:
            raise HTTPException(404, "找不到模型版本。")
        key_for(row)
        current.version_id = row.id
        row.published = True
        return {"id": row.id}


def estimate(text):
    return (len(text.encode()) + 2) // 3


async def metered(
    db, user_id, answer_id, version, key, purpose, messages, caller, limit=None
):
    async with db.begin() as session:
        department = await session.get(DepartmentMember, user_id)
        usage = Usage(
            user_id=user_id,
            department_id=department.org_id,
            answer_id=answer_id,
            version_id=version.id,
            purpose=purpose,
        )
        session.add(usage)
    from telemetry import CALLS

    CALLS.labels(purpose).inc()
    totals = {}
    output_bytes = 0
    used_tool = False
    completed = False
    from contextlib import aclosing

    from anyio import CancelScope

    try:
        async with aclosing(
            caller(
                messages,
                version.base_url,
                version.model,
                key,
                limit or version.output,
                totals,
            )
        ) as stream:
            async for part in stream:
                used_tool |= not isinstance(part, str)
                output_bytes += len(
                    (part if isinstance(part, str) else part.arguments).encode()
                )
                yield part
        completed = True
    finally:
        with CancelScope(shield=True):
            async with db.begin() as session:
                row = await session.get(Usage, usage.id)
                if purpose == "decision" and completed and not used_tool:
                    row.purpose = "answer"
                if "prompt_tokens" in totals and "completion_tokens" in totals:
                    row.source = "reported"
                    row.input_tokens = totals["prompt_tokens"]
                    row.output_tokens = totals["completion_tokens"]
                elif completed or output_bytes:
                    row.source = "estimated"
                    row.input_tokens = sum(
                        estimate(m.get("content") or "") + 6 for m in messages
                    )
                    row.output_tokens = (output_bytes + 2) // 3


@router.post("/admin/models/{version_id}/verify")
async def verify(version_id: UUID, admin: Admin, db: Database):
    async with db() as session:
        version = await session.get(ModelVersion, version_id)
        if not version:
            raise HTTPException(404, "找不到模型版本。")
        key = key_for(version)
    try:
        async for _ in metered(
            db,
            admin.id,
            None,
            version,
            key,
            "verify",
            [{"role": "user", "content": "请回复 OK"}],
            stream_reply,
            32,
        ):
            pass
    except ModelFailure as exc:
        raise HTTPException(502, str(exc)) from None
    return {"ok": True}


@router.get("/usage")
async def stats(
    user: Identity,
    db: Database,
    start: date,
    end: date,
    organization: bool = False,
    department_id: UUID | None = None,
    employee_id: UUID | None = None,
):
    if end < start or (end - start).days > 366:
        raise HTTPException(422, "请选择一年以内的有效日期范围。")
    if (organization or department_id or employee_id) and not user.system_admin:
        raise HTTPException(403, "需要系统管理员权限。")
    stmt = select(Usage).where(
        Usage.created_at >= datetime.combine(start, time.min, ZONE).astimezone(UTC),
        Usage.created_at
        < datetime.combine(end + timedelta(days=1), time.min, ZONE).astimezone(UTC),
    )
    if not organization:
        stmt = stmt.where(Usage.user_id == user.id)
    if employee_id:
        stmt = stmt.where(Usage.user_id == employee_id)
    if department_id:
        stmt = stmt.where(Usage.department_id == department_id)
    async with db() as session:
        rows = await session.scalars(stmt)
        buckets = {}

        for r in rows:
            day = (
                (
                    r.created_at
                    if r.created_at.tzinfo
                    else r.created_at.replace(tzinfo=UTC)
                )
                .astimezone(ZONE)
                .date()
                .isoformat()
            )
            group = buckets.setdefault(
                (day, r.purpose, r.source),
                {"input": 0, "output": 0, "calls": 0, "rounds": set()},
            )
            group["input"] += r.input_tokens or 0
            group["output"] += r.output_tokens or 0
            group["calls"] += 1
            if r.answer_id and r.purpose == "answer":
                group["rounds"].add(r.answer_id)
        return [
            {
                "day": d,
                "purpose": p,
                "source": s,
                "calls": v["calls"],
                "rounds": len(v["rounds"]),
                "input": None if s == "unknown" else v["input"],
                "output": None if s == "unknown" else v["output"],
            }
            for (d, p, s), v in sorted(buckets.items())
        ]
