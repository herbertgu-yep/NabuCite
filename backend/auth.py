import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, SecretStr
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from database import Database, User

router = APIRouter()
passwords = PasswordHasher()
dummy_hash = passwords.hash(secrets.token_urlsafe(32))
lifetime = timedelta(hours=2)


def signing_key():
    key = os.getenv("JWT_SECRET", "")
    if len(key.encode()) < 32:
        raise HTTPException(503, "请在后端配置独立的 JWT 签名密钥（至少 32 字节）。")
    return key


def unauthorized():
    return HTTPException(
        401, "认证失败，请重新登录。", headers={"WWW-Authenticate": "Bearer"}
    )


class Credentials(BaseModel):
    username: str
    password: SecretStr


@router.post("/api/auth/login")
async def login(body: Credentials, db: Database):
    async with db() as session:
        user = await session.scalar(
            select(User).where(User.username == body.username.strip())
        )
    try:
        await run_in_threadpool(
            passwords.verify,
            user.password_hash if user else dummy_hash,
            body.password.get_secret_value(),
        )
    except VerificationError:
        raise unauthorized() from None
    if not user or not user.enabled:
        raise unauthorized()
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(user.id),
            "ver": user.token_version,
            "iat": now,
            "exp": now + lifetime,
            "iss": "nabucite",
        },
        signing_key(),
        algorithm="HS256",
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": int(lifetime.total_seconds()),
    }


async def current_user(
    db: Database,
    bearer: Annotated[
        HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
    ],
):
    if bearer is None:
        raise unauthorized()
    try:
        claims = jwt.decode(
            bearer.credentials,
            signing_key(),
            algorithms=["HS256"],
            issuer="nabucite",
            options={"require": ["sub", "exp", "iat"]},
        )
        user_id = UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise unauthorized() from None
    async with db() as session:
        user = await session.get(User, user_id)
    if user is None or not user.enabled or user.token_version != claims.get("ver", 0):
        raise unauthorized()
    return user


Identity = Annotated[User, Depends(current_user)]
